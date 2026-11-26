import 'dart:async';

import 'package:agy_client/agy_client.dart';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'hosts_state.dart';

enum HostLinkStatus { noHost, racing, connected, offline }

class ActiveConnectionState {
  const ActiveConnectionState({
    this.status = HostLinkStatus.noHost,
    this.host,
    this.activeEndpoint,
    this.latencyMs,
    this.probes = const [],
    this.httpApi,
    this.wsClient,
    this.wsState = WsConnectionState.disconnected,
    this.error,
  });

  final HostLinkStatus status;
  final HostProfile? host;
  final ChannelEndpoint? activeEndpoint;
  final int? latencyMs;
  final List<ChannelProbeResult> probes;
  final AgyHttpApi? httpApi;
  final AgyWsClient? wsClient;
  final WsConnectionState wsState;
  final String? error;

  bool get isReady => status == HostLinkStatus.connected && httpApi != null && wsClient != null;

  ActiveConnectionState copyWith({
    HostLinkStatus? status,
    HostProfile? host,
    ChannelEndpoint? activeEndpoint,
    int? latencyMs,
    List<ChannelProbeResult>? probes,
    AgyHttpApi? httpApi,
    AgyWsClient? wsClient,
    WsConnectionState? wsState,
    String? error,
    bool clearError = false,
  }) {
    return ActiveConnectionState(
      status: status ?? this.status,
      host: host ?? this.host,
      activeEndpoint: activeEndpoint ?? this.activeEndpoint,
      latencyMs: latencyMs ?? this.latencyMs,
      probes: probes ?? this.probes,
      httpApi: httpApi ?? this.httpApi,
      wsClient: wsClient ?? this.wsClient,
      wsState: wsState ?? this.wsState,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

final activeConnectionProvider =
    NotifierProvider<ActiveConnectionNotifier, ActiveConnectionState>(
  ActiveConnectionNotifier.new,
);

class ActiveConnectionNotifier extends Notifier<ActiveConnectionState> {
  StreamSubscription<List<ConnectivityResult>>? _connSub;
  StreamSubscription<WsConnectionState>? _wsStateSub;
  int _generation = 0;

  @override
  ActiveConnectionState build() {
    ref.onDispose(_disposeConnections);

    _connSub?.cancel();
    _connSub = Connectivity().onConnectivityChanged.listen((_) {
      if (state.host != null) {
        unawaited(reconnect());
      }
    });

    final hostsAsync = ref.watch(hostsProvider);
    final host = hostsAsync.valueOrNull?.activeHost;
    if (host == null) {
      unawaited(_teardownTransport());
      return const ActiveConnectionState(status: HostLinkStatus.noHost);
    }

    Future.microtask(() => _connectToHost(host));
    return ActiveConnectionState(
      status: HostLinkStatus.racing,
      host: host,
    );
  }

  Future<void> reconnect() async {
    final host = state.host;
    if (host == null) return;
    await _connectToHost(host);
  }

  Future<void> _connectToHost(HostProfile host) async {
    final gen = ++_generation;
    await _teardownTransport();

    state = ActiveConnectionState(
      status: HostLinkStatus.racing,
      host: host,
    );

    final token = await ref.read(hostsProvider.notifier).readDeviceToken(host.id);
    if (gen != _generation) return;
    if (token == null || token.isEmpty) {
      state = state.copyWith(
        status: HostLinkStatus.offline,
        error: 'Device token missing — please re-pair this host.',
      );
      return;
    }

    final racer = ChannelRacer();
    final probes = await racer.probeAll(host.endpoints);
    if (gen != _generation) return;

    ChannelProbeResult? winner;
    for (final p in probes) {
      if (p.reachable) {
        winner = p;
        break;
      }
    }

    if (winner == null) {
      state = state.copyWith(
        status: HostLinkStatus.offline,
        probes: probes,
        error: 'All configured channels are unreachable.',
      );
      return;
    }

    final api = AgyHttpApi(baseUrl: winner.endpoint.baseUrl, deviceToken: token);
    final ws = AgyWsClient(baseUrl: winner.endpoint.baseUrl, deviceToken: token);

    _wsStateSub = ws.connectionStates.listen((wsState) {
      if (gen != _generation) return;
      state = state.copyWith(wsState: wsState);
    });

    state = ActiveConnectionState(
      status: HostLinkStatus.connected,
      host: host,
      activeEndpoint: winner.endpoint,
      latencyMs: winner.latencyMs,
      probes: probes,
      httpApi: api,
      wsClient: ws,
      wsState: WsConnectionState.connecting,
    );

    await ws.connect();

    // Sync freshly discovered channels (e.g. newly started Cloudflare quick tunnel)
    try {
      final status = await api.getChannels();
      if (gen == _generation && status.endpoints.isNotEmpty) {
        await ref.read(hostsProvider.notifier).updateHostEndpoints(host.id, status.endpoints);
      }
    } catch (_) {}
  }

  Future<void> _teardownTransport() async {
    await _wsStateSub?.cancel();
    _wsStateSub = null;
    state.httpApi?.close();
    await state.wsClient?.dispose();
  }

  void _disposeConnections() {
    _generation++;
    _connSub?.cancel();
    unawaited(_teardownTransport());
  }
}
