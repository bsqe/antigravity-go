import 'dart:convert';
import 'dart:io';

import 'package:agy_client/agy_client.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _prefsHostsKey = 'agy_go_hosts_v1';
const _prefsActiveHostKey = 'agy_go_active_host_v1';

class HostsState {
  const HostsState({
    this.hosts = const [],
    this.activeHostId,
  });

  final List<HostProfile> hosts;
  final String? activeHostId;

  HostProfile? get activeHost {
    if (hosts.isEmpty) return null;
    if (activeHostId != null) {
      for (final h in hosts) {
        if (h.id == activeHostId) return h;
      }
    }
    return hosts.first;
  }

  HostsState copyWith({
    List<HostProfile>? hosts,
    String? activeHostId,
    bool clearActive = false,
  }) {
    return HostsState(
      hosts: hosts ?? this.hosts,
      activeHostId: clearActive ? null : (activeHostId ?? this.activeHostId),
    );
  }
}

final secureStorageProvider = Provider<FlutterSecureStorage>(
  (_) => const FlutterSecureStorage(),
);

final hostsProvider = AsyncNotifierProvider<HostsNotifier, HostsState>(
  HostsNotifier.new,
);

class HostsNotifier extends AsyncNotifier<HostsState> {
  @override
  Future<HostsState> build() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_prefsHostsKey);
    final activeId = prefs.getString(_prefsActiveHostKey);
    if (raw == null || raw.isEmpty) {
      return const HostsState();
    }
    try {
      final decoded = jsonDecode(raw) as List<dynamic>;
      final hosts = decoded
          .whereType<Map<String, dynamic>>()
          .map(HostProfile.fromJson)
          .toList();
      return HostsState(hosts: hosts, activeHostId: activeId);
    } catch (_) {
      return const HostsState();
    }
  }

  Future<String?> readDeviceToken(String hostId) {
    final storage = ref.read(secureStorageProvider);
    return storage.read(key: 'agy_host_token_$hostId');
  }

  Future<void> _persist(HostsState next) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _prefsHostsKey,
      jsonEncode(next.hosts.map((h) => h.toJson()).toList()),
    );
    if (next.activeHostId != null) {
      await prefs.setString(_prefsActiveHostKey, next.activeHostId!);
    } else {
      await prefs.remove(_prefsActiveHostKey);
    }
    state = AsyncData(next);
  }

  /// Pairs using a QR `agygo://pair?...` URI by racing all embedded endpoints,
  /// exchanging the pairing token for a per-device token, and storing the token
  /// exclusively in secure storage.
  Future<HostProfile> pairFromUri(
    String rawUri, {
    String? deviceName,
  }) async {
    final payload = parsePairingUri(rawUri);
    final racer = ChannelRacer();
    final fastest = await racer.selectFastest(payload.endpoints);

    final ordered = <ChannelEndpoint>[];
    if (fastest != null) {
      ordered.add(fastest.endpoint);
    }
    // Prefer remote-routable tunnels before LAN when race did not yield an immediate winner
    final remaining = payload.endpoints
        .where((e) => ordered.every((picked) => picked.baseUrl != e.baseUrl))
        .toList()
      ..sort((a, b) {
        int rank(ChannelKind k) {
          switch (k) {
            case ChannelKind.cloudflare:
              return 0;
            case ChannelKind.tailscale:
              return 1;
            case ChannelKind.lan:
              return 2;
          }
        }

        return rank(a.kind).compareTo(rank(b.kind));
      });
    ordered.addAll(remaining);

    Object? lastError;
    PairResult? pairRes;
    for (final ep in ordered) {
      final api = AgyHttpApi(baseUrl: ep.baseUrl);
      try {
        pairRes = await api.pair(
          pairingToken: payload.pairingToken,
          deviceName: (deviceName != null && deviceName.trim().isNotEmpty)
              ? deviceName.trim()
              : _defaultDeviceName(),
          platform: Platform.operatingSystem,
        );
        break;
      } catch (err) {
        lastError = err;
      } finally {
        api.close();
      }
    }

    if (pairRes == null) {
      throw lastError ?? StateError('All pairing endpoints failed.');
    }

    final hostId = 'host_${DateTime.now().millisecondsSinceEpoch}';
    final storage = ref.read(secureStorageProvider);
    await storage.write(key: 'agy_host_token_$hostId', value: pairRes.deviceToken);

    final profile = HostProfile(
      id: hostId,
      name: pairRes.hostName.isNotEmpty ? pairRes.hostName : payload.hostName,
      deviceId: pairRes.deviceId,
      endpoints: payload.endpoints,
    );

    final cur = state.valueOrNull ?? const HostsState();
    final next = cur.copyWith(
      hosts: [profile, ...cur.hosts],
      activeHostId: profile.id,
    );
    await _persist(next);
    return profile;
  }

  /// Pairs manually by URL + 48-char pairing token (e.g. Tailscale or LAN IP).
  Future<HostProfile> pairManual({
    required String baseUrl,
    required String pairingToken,
    String? hostLabel,
  }) async {
    final cleanUrl = baseUrl.trim().replaceAll(RegExp(r'/+$'), '');
    final kind = cleanUrl.contains('trycloudflare.com') || cleanUrl.startsWith('https://')
        ? ChannelKind.cloudflare
        : cleanUrl.contains('.ts.net') || cleanUrl.contains('100.')
            ? ChannelKind.tailscale
            : ChannelKind.lan;
    final endpoint = ChannelEndpoint(
      kind: kind,
      label: kind.shortLabel,
      baseUrl: cleanUrl,
    );

    final api = AgyHttpApi(baseUrl: cleanUrl);
    try {
      final pairRes = await api.pair(
        pairingToken: pairingToken.trim(),
        deviceName: _defaultDeviceName(),
        platform: Platform.operatingSystem,
      );
      final hostId = 'host_${DateTime.now().millisecondsSinceEpoch}';
      await ref
          .read(secureStorageProvider)
          .write(key: 'agy_host_token_$hostId', value: pairRes.deviceToken);

      final profile = HostProfile(
        id: hostId,
        name: (hostLabel != null && hostLabel.trim().isNotEmpty)
            ? hostLabel.trim()
            : pairRes.hostName,
        deviceId: pairRes.deviceId,
        endpoints: [endpoint],
      );
      final cur = state.valueOrNull ?? const HostsState();
      final next = cur.copyWith(
        hosts: [profile, ...cur.hosts],
        activeHostId: profile.id,
      );
      await _persist(next);
      return profile;
    } finally {
      api.close();
    }
  }

  Future<void> selectHost(String hostId) async {
    final cur = state.valueOrNull ?? const HostsState();
    await _persist(cur.copyWith(activeHostId: hostId));
  }

  Future<void> updateHostEndpoints(String hostId, List<ChannelEndpoint> endpoints) async {
    final cur = state.valueOrNull ?? const HostsState();
    HostProfile? existing;
    for (final h in cur.hosts) {
      if (h.id == hostId) {
        existing = h;
        break;
      }
    }
    if (existing != null) {
      final before = jsonEncode(existing.endpoints.map((e) => e.toJson()).toList());
      final after = jsonEncode(endpoints.map((e) => e.toJson()).toList());
      if (before == after) return;
    }
    final updated = cur.hosts
        .map((h) => h.id == hostId ? h.copyWith(endpoints: endpoints) : h)
        .toList();
    await _persist(cur.copyWith(hosts: updated));
  }

  Future<void> removeHost(String hostId) async {
    final cur = state.valueOrNull ?? const HostsState();
    await ref.read(secureStorageProvider).delete(key: 'agy_host_token_$hostId');
    final remaining = cur.hosts.where((h) => h.id != hostId).toList();
    final nextActive = cur.activeHostId == hostId
        ? (remaining.isNotEmpty ? remaining.first.id : null)
        : cur.activeHostId;
    await _persist(
      cur.copyWith(
        hosts: remaining,
        activeHostId: nextActive,
        clearActive: nextActive == null,
      ),
    );
  }

  String _defaultDeviceName() {
    try {
      final name = Platform.localHostname.trim();
      if (name.isNotEmpty) return name;
    } catch (_) {}
    return 'Antigravity Go Mobile';
  }
}
