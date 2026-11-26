import 'dart:async';
import 'dart:typed_data';

import 'package:agy_client/agy_client.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'connection_state.dart';

class PendingImage {
  const PendingImage({
    required this.bytes,
    required this.filename,
  });

  final Uint8List bytes;
  final String filename;
}

class ChatState {
  const ChatState({
    required this.sessionId,
    this.messages = const [],
    this.hasMore = false,
    this.loading = false,
    this.uploadingImages = false,
    this.activeTurnId,
    this.pendingImages = const [],
    this.error,
  });

  final String sessionId;
  final List<MessageRecord> messages;
  final bool hasMore;
  final bool loading;
  final bool uploadingImages;
  final String? activeTurnId;
  final List<PendingImage> pendingImages;
  final String? error;

  bool get isGenerating => activeTurnId != null;

  ChatState copyWith({
    List<MessageRecord>? messages,
    bool? hasMore,
    bool? loading,
    bool? uploadingImages,
    String? activeTurnId,
    bool clearActiveTurn = false,
    List<PendingImage>? pendingImages,
    String? error,
    bool clearError = false,
  }) {
    return ChatState(
      sessionId: sessionId,
      messages: messages ?? this.messages,
      hasMore: hasMore ?? this.hasMore,
      loading: loading ?? this.loading,
      uploadingImages: uploadingImages ?? this.uploadingImages,
      activeTurnId: clearActiveTurn ? null : (activeTurnId ?? this.activeTurnId),
      pendingImages: pendingImages ?? this.pendingImages,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

final chatProvider =
    NotifierProvider.autoDispose.family<ChatNotifier, ChatState, String>(
  ChatNotifier.new,
);

class ChatNotifier extends AutoDisposeFamilyNotifier<ChatState, String> {
  StreamSubscription<StreamEvent>? _wsSub;
  int _lastSeq = 0;

  @override
  ChatState build(String arg) {
    ref.onDispose(() {
      _wsSub?.cancel();
      ref.read(activeConnectionProvider).wsClient?.unsubscribeSession(arg);
    });

    final conn = ref.watch(activeConnectionProvider);
    _wsSub?.cancel();

    if (!conn.isReady) {
      return ChatState(sessionId: arg);
    }

    _wsSub = conn.wsClient!.events.listen(_onWsEvent);
    Future.microtask(() => _initialLoad(arg));
    return ChatState(sessionId: arg, loading: true);
  }

  Future<void> _initialLoad(String sessionId) async {
    final conn = ref.read(activeConnectionProvider);
    final api = conn.httpApi;
    final ws = conn.wsClient;
    if (api == null || ws == null) return;

    try {
      final page = await api.getMessages(sessionId, limit: 50);
      _lastSeq = page.lastSeq;
      String? runningTurn;
      for (final m in page.messages.reversed) {
        if (m.role == 'assistant' && m.status == 'streaming') {
          runningTurn = m.turnId;
          break;
        }
      }
      state = state.copyWith(
        messages: page.messages,
        hasMore: page.hasMore,
        loading: false,
        activeTurnId: runningTurn,
        clearActiveTurn: runningTurn == null,
      );
      ws.subscribeSession(sessionId, lastSeq: _lastSeq);
    } catch (err) {
      state = state.copyWith(loading: false, error: err.toString());
    }
  }

  Future<void> loadOlderMessages() async {
    if (!state.hasMore || state.loading || state.messages.isEmpty) return;
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null) return;
    final oldestSeq = state.messages.first.seq;
    state = state.copyWith(loading: true);
    try {
      final page = await api.getMessages(
        state.sessionId,
        beforeSeq: oldestSeq,
        limit: 50,
      );
      state = state.copyWith(
        messages: [...page.messages, ...state.messages],
        hasMore: page.hasMore,
        loading: false,
      );
    } catch (err) {
      state = state.copyWith(loading: false, error: err.toString());
    }
  }

  void addPendingImage(PendingImage image) {
    state = state.copyWith(pendingImages: [...state.pendingImages, image]);
  }

  void removePendingImageAt(int index) {
    final next = [...state.pendingImages];
    if (index >= 0 && index < next.length) {
      next.removeAt(index);
      state = state.copyWith(pendingImages: next);
    }
  }

  Future<void> sendMessage(String rawText, {String? model}) async {
    final text = rawText.trim();
    if (text.isEmpty && state.pendingImages.isEmpty) return;

    final conn = ref.read(activeConnectionProvider);
    final api = conn.httpApi;
    final ws = conn.wsClient;
    if (api == null || ws == null) {
      state = state.copyWith(error: 'Not connected to Bridge');
      return;
    }

    final imagesToUpload = [...state.pendingImages];
    final uploadedIds = <String>[];
    if (imagesToUpload.isNotEmpty) {
      state = state.copyWith(uploadingImages: true, clearError: true);
      try {
        for (final img in imagesToUpload) {
          final info = await api.uploadImage(
            sessionId: state.sessionId,
            bytes: img.bytes,
            filename: img.filename,
          );
          uploadedIds.add(info.id);
        }
      } catch (err) {
        state = state.copyWith(uploadingImages: false, error: 'Image upload failed: $err');
        return;
      }
      state = state.copyWith(uploadingImages: false, pendingImages: const []);
    }

    final turnId = 'turn_${DateTime.now().millisecondsSinceEpoch}';
    state = state.copyWith(activeTurnId: turnId, clearError: true);
    ws.sendTurn(
      sessionId: state.sessionId,
      turnId: turnId,
      text: text,
      attachmentIds: uploadedIds,
      model: model,
    );
  }

  void cancelActiveTurn() {
    final turnId = state.activeTurnId;
    if (turnId == null) return;
    ref.read(activeConnectionProvider).wsClient?.cancelTurn(
          sessionId: state.sessionId,
          turnId: turnId,
        );
  }

  void _onWsEvent(StreamEvent ev) {
    if (ev.sessionId != state.sessionId) return;
    if (ev.seq != null && ev.seq! > _lastSeq) {
      _lastSeq = ev.seq!;
    }

    switch (ev.type) {
      case 'turn.started':
        final turnId = ev.turnId ?? '';
        final userId = (ev.data['userMessageId'] ?? 'user_$turnId').toString();
        final asstId = (ev.data['assistantMessageId'] ?? ev.messageId ?? 'asst_$turnId').toString();
        final prompt = (ev.data['prompt'] ?? '').toString();
        final rawAtts = (ev.data['attachments'] as List<dynamic>? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(AttachmentInfo.fromJson)
            .toList();

        final next = [...state.messages];
        if (!next.any((m) => m.id == userId)) {
          next.add(
            MessageRecord(
              id: userId,
              sessionId: state.sessionId,
              turnId: turnId,
              role: 'user',
              content: prompt,
              attachments: rawAtts,
              status: 'completed',
              seq: (ev.seq ?? _lastSeq) - 1,
              createdAtMs: ev.timestampMs,
              updatedAtMs: ev.timestampMs,
            ),
          );
        }
        if (!next.any((m) => m.id == asstId)) {
          next.add(
            MessageRecord(
              id: asstId,
              sessionId: state.sessionId,
              turnId: turnId,
              role: 'assistant',
              content: '',
              thoughts: '',
              status: 'streaming',
              seq: ev.seq ?? _lastSeq,
              createdAtMs: ev.timestampMs,
              updatedAtMs: ev.timestampMs,
            ),
          );
        }
        state = state.copyWith(messages: next, activeTurnId: turnId);
        break;

      case 'thought.delta':
        _mutateAssistant(
          ev.messageId,
          ev.turnId,
          (m) => m.copyWith(
            thoughts: m.thoughts + (ev.data['delta'] ?? '').toString(),
            updatedAtMs: ev.timestampMs,
          ),
        );
        break;

      case 'text.delta':
        _mutateAssistant(
          ev.messageId,
          ev.turnId,
          (m) => m.copyWith(
            content: m.content + (ev.data['delta'] ?? '').toString(),
            updatedAtMs: ev.timestampMs,
          ),
        );
        break;

      case 'tool.call':
      case 'tool.result':
        final toolId = (ev.data['id'] ?? ev.data['name'] ?? 'tool').toString();
        final rec = ToolEventRecord(
          id: toolId,
          name: (ev.data['name'] ?? 'tool').toString(),
          args: (ev.data['args'] as Map<String, dynamic>?) ?? const {},
          status: ev.type == 'tool.result' ? 'completed' : 'running',
          outputPreview: ev.data['outputPreview']?.toString(),
        );
        _mutateAssistant(ev.messageId, ev.turnId, (m) {
          final tools = [...m.toolEvents];
          final idx = tools.indexWhere((t) => t.id == toolId);
          if (idx >= 0) {
            tools[idx] = rec;
          } else {
            tools.add(rec);
          }
          return m.copyWith(toolEvents: tools, updatedAtMs: ev.timestampMs);
        });
        break;

      case 'turn.done':
        final finalStatus = (ev.data['status'] ?? 'completed').toString();
        final errText = ev.data['error']?.toString();
        _mutateAssistant(
          ev.messageId,
          ev.turnId,
          (m) => m.copyWith(
            status: finalStatus,
            errorMessage: errText,
            updatedAtMs: ev.timestampMs,
          ),
        );
        state = state.copyWith(
          clearActiveTurn: state.activeTurnId == ev.turnId || state.activeTurnId == null,
          error: errText,
        );
        break;

      case 'error':
        state = state.copyWith(
          error: (ev.data['detail'] ?? 'Bridge error').toString(),
          clearActiveTurn: ev.turnId != null && ev.turnId == state.activeTurnId,
        );
        break;
    }
  }

  void _mutateAssistant(
    String? messageId,
    String? turnId,
    MessageRecord Function(MessageRecord current) transform,
  ) {
    final list = [...state.messages];
    final idx = list.lastIndexWhere(
      (m) =>
          m.role == 'assistant' &&
          ((messageId != null && m.id == messageId) || (turnId != null && m.turnId == turnId)),
    );
    if (idx < 0) return;
    list[idx] = transform(list[idx]);
    state = state.copyWith(messages: list);
  }
}
