import 'package:agy_client/agy_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/connection_state.dart';
import '../../state/desktop_conversations_state.dart';
import '../../state/hosts_state.dart';
import '../../state/projects_sessions_state.dart';
import '../chat/chat_screen.dart';
import '../desktop/desktop_conversation_screen.dart';
import '../hosts/qr_pair_screen.dart';
import '../settings/channel_status_screen.dart';

class HomeShell extends ConsumerWidget {
  const HomeShell({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final hostsAsync = ref.watch(hostsProvider);
    final conn = ref.watch(activeConnectionProvider);
    final workspace = ref.watch(workspaceProvider);
    final isWide = MediaQuery.sizeOf(context).width >= 840;

    return hostsAsync.when(
      loading: () => const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (err, _) => Scaffold(body: Center(child: Text('加载主机失败: $err'))),
      data: (hostsState) {
        if (hostsState.hosts.isEmpty) {
          return _buildOnboarding(context);
        }

        final sidebar = _WorkspaceSidebar(
          conn: conn,
          workspace: workspace,
          closeDrawerOnSelect: !isWide,
        );

        final activeSession = workspace.activeSession;
        final body = !conn.isReady
            ? _buildConnectionBanner(context, ref, conn)
            : activeSession == null
                ? _buildEmptyWorkspace(context, ref, workspace)
                : ChatScreen(
                    sessionId: activeSession.id,
                    sourceDesktopId: activeSession.sourceDesktopId,
                  );

        return Scaffold(
          appBar: AppBar(
            title: Text(activeSession?.title ?? conn.host?.name ?? 'Antigravity Go'),
            actions: [
              _ChannelStatusPill(conn: conn),
              const SizedBox(width: 8),
            ],
          ),
          drawer: isWide ? null : Drawer(child: sidebar),
          body: isWide
              ? Row(
                  children: [
                    SizedBox(width: 300, child: Material(elevation: 1, child: sidebar)),
                    const VerticalDivider(width: 1),
                    Expanded(child: body),
                  ],
                )
              : body,
        );
      },
    );
  }

  Widget _buildOnboarding(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.rocket_launch_outlined,
                  size: 64,
                  color: Theme.of(context).colorScheme.primary,
                ),
                const SizedBox(height: 16),
                Text(
                  'Antigravity Go',
                  style: Theme.of(context).textTheme.headlineMedium,
                ),
                const SizedBox(height: 8),
                Text(
                  '在电脑端运行 agy-bridge start（或启用 Antigravity Sidecar 插件），扫码即可通过局域网 / Tailscale / Cloudflare 极速遥控。',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                ),
                const SizedBox(height: 24),
                FilledButton.icon(
                  onPressed: () {
                    Navigator.of(context).push(
                      MaterialPageRoute<void>(builder: (_) => const QrPairScreen()),
                    );
                  },
                  icon: const Icon(Icons.qr_code_scanner),
                  label: const Text('扫码配对电脑'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildConnectionBanner(
    BuildContext context,
    WidgetRef ref,
    ActiveConnectionState conn,
  ) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (conn.status == HostLinkStatus.racing) ...[
              const CircularProgressIndicator(),
              const SizedBox(height: 16),
              const Text('正在并行测速 LAN / Tailscale / Cloudflare 通道…'),
            ] else ...[
              Icon(
                Icons.cloud_off,
                size: 48,
                color: Theme.of(context).colorScheme.error,
              ),
              const SizedBox(height: 12),
              Text(conn.error ?? '无法连接到电脑端 Bridge'),
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: () => ref.read(activeConnectionProvider.notifier).reconnect(),
                icon: const Icon(Icons.refresh),
                label: const Text('重新连接'),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildEmptyWorkspace(
    BuildContext context,
    WidgetRef ref,
    WorkspaceState workspace,
  ) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.chat_bubble_outline, size: 48),
            const SizedBox(height: 12),
            Text(
              workspace.projects.isEmpty
                  ? '还没有登记工作区项目，请先在侧边栏新增项目目录'
                  : '在左侧选择一个会话，或新建对话开始使用',
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            if (workspace.projects.isNotEmpty)
              FilledButton.icon(
                onPressed: () => ref
                    .read(workspaceProvider.notifier)
                    .createSession(projectId: workspace.projects.first.id),
                icon: const Icon(Icons.add),
                label: const Text('新建对话'),
              ),
          ],
        ),
      ),
    );
  }
}

class _ChannelStatusPill extends StatelessWidget {
  const _ChannelStatusPill({required this.conn});

  final ActiveConnectionState conn;

  @override
  Widget build(BuildContext context) {
    final ep = conn.activeEndpoint;
    final label = conn.status == HostLinkStatus.racing
        ? '测速中'
        : ep != null
            ? '${ep.kind.shortLabel} ${conn.latencyMs ?? 0}ms'
            : '离线';
    final color = ep != null && conn.wsState == WsConnectionState.authenticated
        ? Colors.green
        : conn.status == HostLinkStatus.racing
            ? Colors.orange
            : Theme.of(context).colorScheme.error;

    return ActionChip(
      avatar: CircleAvatar(radius: 5, backgroundColor: color),
      label: Text(label),
      onPressed: () {
        Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => const ChannelStatusScreen()),
        );
      },
    );
  }
}

class _WorkspaceSidebar extends ConsumerStatefulWidget {
  const _WorkspaceSidebar({
    required this.conn,
    required this.workspace,
    required this.closeDrawerOnSelect,
  });

  final ActiveConnectionState conn;
  final WorkspaceState workspace;
  final bool closeDrawerOnSelect;

  @override
  ConsumerState<_WorkspaceSidebar> createState() => _WorkspaceSidebarState();
}

class _WorkspaceSidebarState extends ConsumerState<_WorkspaceSidebar> {
  int _tabIndex = 0; // 0 = Mobile/Bridge sessions, 1 = Desktop Antigravity conversations

  Future<void> _showAddProjectDialog(BuildContext context) async {
    final nameCtrl = TextEditingController();
    final pathCtrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('新增项目工作区'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: nameCtrl,
              decoration: const InputDecoration(labelText: '项目名称'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: pathCtrl,
              decoration: const InputDecoration(
                labelText: '电脑本地路径',
                hintText: r'如 E:\work\my-app 或 ~/projects/my-app',
              ),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('添加')),
        ],
      ),
    );
    if (ok == true && pathCtrl.text.trim().isNotEmpty) {
      await ref.read(workspaceProvider.notifier).createProject(
            name: nameCtrl.text.trim().isEmpty ? 'Workspace' : nameCtrl.text.trim(),
            path: pathCtrl.text.trim(),
          );
    }
    nameCtrl.dispose();
    pathCtrl.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final conn = widget.conn;
    final workspace = widget.workspace;
    final closeDrawerOnSelect = widget.closeDrawerOnSelect;
    final notifier = ref.read(workspaceProvider.notifier);
    final desktopState = ref.watch(desktopConversationsProvider);

    return SafeArea(
      child: Column(
        children: [
          ListTile(
            leading: const Icon(Icons.computer),
            title: Text(conn.host?.name ?? '未选择主机'),
            subtitle: Text(conn.activeEndpoint?.label ?? '未连接'),
            trailing: IconButton(
              tooltip: '主机与通道设置',
              icon: const Icon(Icons.tune),
              onPressed: () {
                if (closeDrawerOnSelect) Navigator.pop(context);
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const ChannelStatusScreen()),
                );
              },
            ),
          ),
          const Divider(height: 1),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
            child: SegmentedButton<int>(
              showSelectedIcon: false,
              segments: const [
                ButtonSegment<int>(
                  value: 0,
                  icon: Icon(Icons.chat_bubble_outline, size: 16),
                  label: Text('手机会话'),
                ),
                ButtonSegment<int>(
                  value: 1,
                  icon: Icon(Icons.desktop_windows_outlined, size: 16),
                  label: Text('电脑对话'),
                ),
              ],
              selected: {_tabIndex},
              onSelectionChanged: (next) {
                if (next.isEmpty) return;
                setState(() => _tabIndex = next.first);
                if (next.first == 1 && conn.isReady) {
                  ref.read(desktopConversationsProvider.notifier).refresh(silent: true);
                }
              },
            ),
          ),
          if (_tabIndex == 0) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 4),
              child: Row(
                children: [
                  Text('项目工作区', style: Theme.of(context).textTheme.labelLarge),
                  const Spacer(),
                  IconButton(
                    tooltip: '新增项目',
                    icon: const Icon(Icons.create_new_folder_outlined, size: 20),
                    onPressed: conn.isReady ? () => _showAddProjectDialog(context) : null,
                  ),
                  IconButton(
                    tooltip: '新建会话',
                    icon: const Icon(Icons.add_comment_outlined, size: 20),
                    onPressed: conn.isReady && workspace.projects.isNotEmpty
                        ? () async {
                            final pid =
                                workspace.selectedProjectId ?? workspace.projects.first.id;
                            await notifier.createSession(projectId: pid);
                            if (closeDrawerOnSelect && context.mounted) Navigator.pop(context);
                          }
                        : null,
                  ),
                ],
              ),
            ),
            if (workspace.projects.isNotEmpty)
              SizedBox(
                height: 40,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 10),
                  children: [
                    Padding(
                      padding: const EdgeInsets.only(right: 6),
                      child: ChoiceChip(
                        label: const Text('全部'),
                        selected: workspace.selectedProjectId == null,
                        onSelected: (_) => notifier.setProjectFilter(null),
                      ),
                    ),
                    ...workspace.projects.map(
                      (p) => Padding(
                        padding: const EdgeInsets.only(right: 6),
                        child: ChoiceChip(
                          label: Text(p.name),
                          selected: workspace.selectedProjectId == p.id,
                          onSelected: (_) => notifier.setProjectFilter(p.id),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            const Divider(height: 8),
            Expanded(
              child: RefreshIndicator(
                onRefresh: notifier.refresh,
                child: ListView.builder(
                  itemCount: workspace.visibleSessions.length,
                  itemBuilder: (context, index) {
                    final s = workspace.visibleSessions[index];
                    final selected = s.id == workspace.activeSessionId;
                    return ListTile(
                      selected: selected,
                      leading: Icon(
                        s.status == 'running'
                            ? Icons.sync
                            : s.sourceDesktopId != null
                                ? Icons.desktop_windows_outlined
                                : Icons.chat_bubble_outline,
                        size: 18,
                      ),
                      title: Text(s.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                      subtitle: Text(
                        s.sourceDesktopId != null ? '${s.projectName} · 接力' : s.projectName,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      trailing: IconButton(
                        icon: const Icon(Icons.delete_outline, size: 18),
                        onPressed: () => notifier.deleteSession(s.id),
                      ),
                      onTap: () {
                        notifier.selectSession(s.id);
                        if (closeDrawerOnSelect) Navigator.pop(context);
                      },
                    );
                  },
                ),
              ),
            ),
          ] else ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 4),
              child: Row(
                children: [
                  Text('电脑端 Antigravity 对话', style: Theme.of(context).textTheme.labelLarge),
                  const Spacer(),
                  IconButton(
                    tooltip: '刷新电脑对话',
                    icon: const Icon(Icons.refresh, size: 20),
                    onPressed: conn.isReady
                        ? () => ref.read(desktopConversationsProvider.notifier).refresh()
                        : null,
                  ),
                ],
              ),
            ),
            const Divider(height: 8),
            Expanded(
              child: _buildDesktopList(context, desktopState, closeDrawerOnSelect),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildDesktopList(
    BuildContext context,
    DesktopConversationsState desktopState,
    bool closeDrawerOnSelect,
  ) {
    if (desktopState.loading && desktopState.conversations.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (desktopState.error != null && desktopState.conversations.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            '读取电脑端对话失败: ${desktopState.error}',
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
      );
    }
    if (desktopState.conversations.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            '暂未扫描到电脑端 Antigravity 对话记录\n下拉可手动刷新',
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
          ),
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: () => ref.read(desktopConversationsProvider.notifier).refresh(),
      child: ListView.builder(
        itemCount: desktopState.conversations.length,
        itemBuilder: (context, index) {
          final item = desktopState.conversations[index];
          final meta = StringBuffer('${item.turnCount} 轮');
          if (item.artifacts.isNotEmpty) {
            meta.write(' · ${item.artifacts.length} 文档');
          }
          if (item.linkedSessionId != null) {
            meta.write(' · 已接力');
          }
          return ListTile(
            leading: Icon(
              item.linkedSessionId != null
                  ? Icons.sync_alt_rounded
                  : Icons.history_rounded,
              size: 18,
              color: item.linkedSessionId != null
                  ? Theme.of(context).colorScheme.primary
                  : null,
            ),
            title: Text(item.title, maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text(
              item.preview.isNotEmpty ? '$meta\n${item.preview}' : meta.toString(),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            isThreeLine: item.preview.isNotEmpty,
            onTap: () {
              final nav = Navigator.of(context);
              if (closeDrawerOnSelect) nav.pop();
              nav.push(
                MaterialPageRoute<void>(
                  builder: (_) => DesktopConversationScreen(
                    conversationId: item.id,
                    initialTitle: item.title,
                  ),
                ),
              );
            },
          );
        },
      ),
    );
  }
}
