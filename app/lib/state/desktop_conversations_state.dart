import 'dart:async';

import 'package:agy_client/agy_client.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'connection_state.dart';
import 'projects_sessions_state.dart';

class DesktopConversationsState {
  const DesktopConversationsState({
    this.conversations = const [],
    this.loading = false,
    this.error,
  });

  final List<DesktopConversationSummary> conversations;
  final bool loading;
  final String? error;

  DesktopConversationsState copyWith({
    List<DesktopConversationSummary>? conversations,
    bool? loading,
    String? error,
    bool clearError = false,
  }) {
    return DesktopConversationsState(
      conversations: conversations ?? this.conversations,
      loading: loading ?? this.loading,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

final desktopConversationsProvider =
    NotifierProvider<DesktopConversationsNotifier, DesktopConversationsState>(
  DesktopConversationsNotifier.new,
);

final desktopConversationDetailProvider = FutureProvider.autoDispose
    .family<DesktopConversationDetail, String>((ref, convId) async {
  final conn = ref.watch(activeConnectionProvider);
  final api = conn.httpApi;
  if (!conn.isReady || api == null) {
    throw StateError('尚未连接到电脑端 Bridge');
  }
  return api.getDesktopConversation(convId);
});

class DesktopConversationsNotifier extends Notifier<DesktopConversationsState> {
  StreamSubscription<StreamEvent>? _wsSub;
  DateTime _lastHookRefresh = DateTime.fromMillisecondsSinceEpoch(0);

  @override
  DesktopConversationsState build() {
    ref.onDispose(() => _wsSub?.cancel());
    final conn = ref.watch(activeConnectionProvider);
    _wsSub?.cancel();

    if (!conn.isReady) {
      return const DesktopConversationsState();
    }

    _wsSub = conn.wsClient!.events.listen(_onWsEvent);
    Future.microtask(refresh);
    return const DesktopConversationsState(loading: true);
  }

  void _onWsEvent(StreamEvent ev) {
    if (ev.type != 'hook.event') return;
    final now = DateTime.now();
    if (now.difference(_lastHookRefresh).inSeconds < 2) return;
    _lastHookRefresh = now;
    unawaited(refresh(silent: true));
  }

  Future<void> refresh({bool silent = false}) async {
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null) return;
    if (!silent) {
      state = state.copyWith(loading: true, clearError: true);
    }
    try {
      final list = await api.listDesktopConversations();
      state = state.copyWith(
        conversations: list,
        loading: false,
        clearError: true,
      );
    } catch (err) {
      state = state.copyWith(loading: false, error: err.toString());
    }
  }

  Future<DesktopContinueResult> continueConversation({
    required String conversationId,
    required String projectId,
    bool reuseExisting = true,
  }) async {
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null) throw StateError('尚未连接到电脑端 Bridge');
    final result = await api.continueDesktopConversation(
      conversationId,
      projectId: projectId,
      reuseExisting: reuseExisting,
    );
    await ref.read(workspaceProvider.notifier).refresh();
    ref.read(workspaceProvider.notifier).selectSession(result.session.id);
    unawaited(refresh(silent: true));
    return result;
  }
}
