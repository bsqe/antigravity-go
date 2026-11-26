import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'models.dart';

enum WsConnectionState { disconnected, connecting, authenticated, reconnecting }

/// Resilient authenticated WebSocket client with first-frame auth, heartbeat,
/// exponential backoff reconnection, and `lastSeq` event replay.
class AgyWsClient {
  AgyWsClient({
    required String baseUrl,
    required this.deviceToken,
    WebSocketChannel Function(Uri uri)? channelFactory,
  })  : _wsUri = _toWsUri(baseUrl),
        _channelFactory = channelFactory ?? WebSocketChannel.connect;

  final Uri _wsUri;
  final String deviceToken;
  final WebSocketChannel Function(Uri uri) _channelFactory;

  final _eventsController = StreamController<StreamEvent>.broadcast();
  final _stateController = StreamController<WsConnectionState>.broadcast();
  final Map<String, int> _subscribedLastSeq = {};

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _sub;
  Timer? _pingTimer;
  Timer? _reconnectTimer;
  bool _disposed = false;
  int _reconnectAttempt = 0;
  WsConnectionState _state = WsConnectionState.disconnected;

  Stream<StreamEvent> get events => _eventsController.stream;
  Stream<WsConnectionState> get connectionStates => _stateController.stream;
  WsConnectionState get state => _state;

  static Uri _toWsUri(String baseUrl) {
    final httpUri = Uri.parse(baseUrl.replaceAll(RegExp(r'/+$'), ''));
    final scheme = httpUri.scheme == 'https' ? 'wss' : 'ws';
    return httpUri.replace(scheme: scheme, path: '${httpUri.path}/api/v1/ws');
  }

  void _setState(WsConnectionState next) {
    if (_state == next) return;
    _state = next;
    if (!_stateController.isClosed) _stateController.add(next);
  }

  Future<void> connect() async {
    if (_disposed) return;
    _reconnectTimer?.cancel();
    _setState(_reconnectAttempt == 0
        ? WsConnectionState.connecting
        : WsConnectionState.reconnecting);

    try {
      final ch = _channelFactory(_wsUri);
      _channel = ch;
      _sub = ch.stream.listen(
        _onRawMessage,
        onError: (_) => _scheduleReconnect(),
        onDone: _scheduleReconnect,
        cancelOnError: false,
      );
      _sendRaw({'type': 'auth', 'token': deviceToken});
    } catch (_) {
      _scheduleReconnect();
    }
  }

  void _onRawMessage(dynamic raw) {
    if (raw is! String) return;
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic>) return;
      final ev = StreamEvent.fromJson(decoded);

      if (ev.type == 'auth.ok') {
        _reconnectAttempt = 0;
        _setState(WsConnectionState.authenticated);
        _startHeartbeat();
        // Re-subscribe all tracked sessions with their latest seen sequence
        for (final entry in _subscribedLastSeq.entries) {
          _sendRaw({
            'type': 'session.subscribe',
            'sessionId': entry.key,
            'lastSeq': entry.value,
          });
        }
      } else if (ev.type == 'auth.error') {
        _setState(WsConnectionState.disconnected);
        _eventsController.add(ev);
        return;
      }

      if (ev.sessionId != null && ev.seq != null) {
        final prev = _subscribedLastSeq[ev.sessionId!] ?? 0;
        if (ev.seq! > prev) {
          _subscribedLastSeq[ev.sessionId!] = ev.seq!;
        }
      }

      if (!_eventsController.isClosed) {
        _eventsController.add(ev);
      }
    } catch (_) {}
  }

  void _startHeartbeat() {
    _pingTimer?.cancel();
    _pingTimer = Timer.periodic(const Duration(seconds: 25), (_) {
      if (_state == WsConnectionState.authenticated) {
        _sendRaw({'type': 'ping'});
      }
    });
  }

  void _scheduleReconnect() {
    _pingTimer?.cancel();
    _sub?.cancel();
    _sub = null;
    _channel = null;
    if (_disposed) {
      _setState(WsConnectionState.disconnected);
      return;
    }
    _reconnectAttempt += 1;
    final delaySec = min(30, 1 << min(_reconnectAttempt - 1, 5));
    _setState(WsConnectionState.reconnecting);
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(Duration(seconds: delaySec), connect);
  }

  void _sendRaw(Map<String, dynamic> payload) {
    final ch = _channel;
    if (ch == null) return;
    try {
      ch.sink.add(jsonEncode(payload));
    } catch (_) {}
  }

  void subscribeSession(String sessionId, {int lastSeq = 0}) {
    _subscribedLastSeq[sessionId] = max(_subscribedLastSeq[sessionId] ?? 0, lastSeq);
    if (_state == WsConnectionState.authenticated) {
      _sendRaw({
        'type': 'session.subscribe',
        'sessionId': sessionId,
        'lastSeq': _subscribedLastSeq[sessionId],
      });
    }
  }

  void unsubscribeSession(String sessionId) {
    _subscribedLastSeq.remove(sessionId);
    if (_state == WsConnectionState.authenticated) {
      _sendRaw({'type': 'session.unsubscribe', 'sessionId': sessionId});
    }
  }

  void sendTurn({
    required String sessionId,
    required String turnId,
    required String text,
    List<String> attachmentIds = const [],
    String? model,
  }) {
    _sendRaw({
      'type': 'turn.send',
      'sessionId': sessionId,
      'turnId': turnId,
      'text': text,
      'attachmentIds': attachmentIds,
      if (model != null && model.isNotEmpty) 'model': model,
    });
  }

  void cancelTurn({required String sessionId, String? turnId}) {
    _sendRaw({
      'type': 'turn.cancel',
      'sessionId': sessionId,
      if (turnId != null) 'turnId': turnId,
    });
  }

  Future<void> dispose() async {
    _disposed = true;
    _pingTimer?.cancel();
    _reconnectTimer?.cancel();
    await _sub?.cancel();
    await _channel?.sink.close();
    await _eventsController.close();
    await _stateController.close();
  }
}
