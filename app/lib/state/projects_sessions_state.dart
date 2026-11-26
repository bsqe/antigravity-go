import 'dart:async';

import 'package:agy_client/agy_client.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'connection_state.dart';

class WorkspaceState {
  const WorkspaceState({
    this.projects = const [],
    this.sessions = const [],
    this.selectedProjectId,
    this.activeSessionId,
    this.loading = false,
    this.error,
  });

  final List<ProjectInfo> projects;
  final List<SessionInfo> sessions;
  final String? selectedProjectId;
  final String? activeSessionId;
  final bool loading;
  final String? error;

  List<SessionInfo> get visibleSessions {
    if (selectedProjectId == null) return sessions;
    return sessions.where((s) => s.projectId == selectedProjectId).toList();
  }

  SessionInfo? get activeSession {
    if (activeSessionId == null) return null;
    for (final s in sessions) {
      if (s.id == activeSessionId) return s;
    }
    return null;
  }

  WorkspaceState copyWith({
    List<ProjectInfo>? projects,
    List<SessionInfo>? sessions,
    String? selectedProjectId,
    bool clearProjectFilter = false,
    String? activeSessionId,
    bool clearActiveSession = false,
    bool? loading,
    String? error,
    bool clearError = false,
  }) {
    return WorkspaceState(
      projects: projects ?? this.projects,
      sessions: sessions ?? this.sessions,
      selectedProjectId:
          clearProjectFilter ? null : (selectedProjectId ?? this.selectedProjectId),
      activeSessionId:
          clearActiveSession ? null : (activeSessionId ?? this.activeSessionId),
      loading: loading ?? this.loading,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

final workspaceProvider = NotifierProvider<WorkspaceNotifier, WorkspaceState>(
  WorkspaceNotifier.new,
);

class WorkspaceNotifier extends Notifier<WorkspaceState> {
  StreamSubscription<StreamEvent>? _wsSub;

  @override
  WorkspaceState build() {
    ref.onDispose(() => _wsSub?.cancel());
    final conn = ref.watch(activeConnectionProvider);
    _wsSub?.cancel();

    if (!conn.isReady) {
      return const WorkspaceState();
    }

    _wsSub = conn.wsClient!.events.listen(_handleStreamEvent);
    Future.microtask(refresh);
    return const WorkspaceState(loading: true);
  }

  void _handleStreamEvent(StreamEvent ev) {
    final sid = ev.sessionId;
    if (sid == null) return;
    if (ev.type == 'turn.started') {
      _patchSessionStatus(sid, 'running');
    } else if (ev.type == 'turn.done') {
      final st = ev.data['status']?.toString() == 'error' ? 'error' : 'idle';
      _patchSessionStatus(sid, st);
    }
  }

  void _patchSessionStatus(String sessionId, String nextStatus) {
    final updated = state.sessions
        .map((s) => s.id == sessionId ? s.copyWith(status: nextStatus) : s)
        .toList();
    state = state.copyWith(sessions: updated);
  }

  Future<void> refresh() async {
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null) return;
    state = state.copyWith(loading: true, clearError: true);
    try {
      final results = await Future.wait([api.listProjects(), api.listSessions()]);
      final projects = results[0] as List<ProjectInfo>;
      final sessions = results[1] as List<SessionInfo>;
      var activeId = state.activeSessionId;
      if (activeId == null || !sessions.any((s) => s.id == activeId)) {
        activeId = sessions.isNotEmpty ? sessions.first.id : null;
      }
      state = state.copyWith(
        projects: projects,
        sessions: sessions,
        activeSessionId: activeId,
        clearActiveSession: activeId == null,
        loading: false,
      );
    } catch (err) {
      state = state.copyWith(loading: false, error: err.toString());
    }
  }

  void setProjectFilter(String? projectId) {
    state = state.copyWith(
      selectedProjectId: projectId,
      clearProjectFilter: projectId == null,
    );
  }

  void selectSession(String sessionId) {
    state = state.copyWith(activeSessionId: sessionId);
  }

  Future<ProjectInfo> createProject({required String name, required String path}) async {
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null) throw StateError('Not connected');
    final created = await api.createProject(name: name, path: path);
    await refresh();
    return created;
  }

  Future<SessionInfo> createSession({
    required String projectId,
    String title = 'New Conversation',
    String? model,
  }) async {
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null) throw StateError('Not connected');
    final created = await api.createSession(
      projectId: projectId,
      title: title,
      model: model,
    );
    state = state.copyWith(
      sessions: [created, ...state.sessions],
      activeSessionId: created.id,
    );
    return created;
  }

  Future<void> deleteSession(String sessionId) async {
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null) return;
    await api.deleteSession(sessionId);
    final remaining = state.sessions.where((s) => s.id != sessionId).toList();
    final nextActive = state.activeSessionId == sessionId
        ? (remaining.isNotEmpty ? remaining.first.id : null)
        : state.activeSessionId;
    state = state.copyWith(
      sessions: remaining,
      activeSessionId: nextActive,
      clearActiveSession: nextActive == null,
    );
  }
}
