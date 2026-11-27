import 'package:agy_client/agy_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/desktop_conversations_state.dart';
import '../../state/projects_sessions_state.dart';
import '../chat/widgets/message_bubble.dart';

class DesktopConversationScreen extends ConsumerStatefulWidget {
  const DesktopConversationScreen({
    super.key,
    required this.conversationId,
    this.initialTitle,
  });

  final String conversationId;
  final String? initialTitle;

  @override
  ConsumerState<DesktopConversationScreen> createState() =>
      _DesktopConversationScreenState();
}

class _DesktopConversationScreenState
    extends ConsumerState<DesktopConversationScreen> {
  bool _continuing = false;

  Future<void> _handleContinue(DesktopConversationSummary summary) async {
    if (_continuing) return;
    final workspace = ref.read(workspaceProvider);
    final projects = workspace.projects;
    if (projects.isEmpty) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('请先在侧边栏「手机会话」中登记一个电脑项目工作区目录')),
        );
      }
      return;
    }

    final chosenProjectId = projects.length == 1
        ? projects.first.id
        : await _pickProject(projects, summary.inferredProjectId);
    if (chosenProjectId == null || !mounted) return;

    setState(() => _continuing = true);
    try {
      final result = await ref
          .read(desktopConversationsProvider.notifier)
          .continueConversation(
            conversationId: summary.id,
            projectId: chosenProjectId,
            reuseExisting: true,
          );
      if (!mounted) return;
      final hint = result.reused
          ? (result.importedMessages > 0
              ? '已切回接力会话，并增量同步 ${result.importedMessages} 条新记录'
              : '已切回接力会话，可直接继续操作')
          : '已导入 ${result.importedMessages} 条电脑端对话，开始接力操作';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(hint)));
      Navigator.of(context).pop(true);
    } catch (err) {
      if (mounted) {
        setState(() => _continuing = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('接力失败: $err')),
        );
      }
    }
  }

  Future<String?> _pickProject(
    List<ProjectInfo> projects,
    String? suggestedProjectId,
  ) async {
    var selected = suggestedProjectId ?? projects.first.id;
    if (!projects.any((p) => p.id == selected)) {
      selected = projects.first.id;
    }
    return showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheetState) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  '选择接力运行的电脑项目目录',
                  style: Theme.of(ctx).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                ...projects.map(
                  (p) => ListTile(
                    dense: true,
                    leading: Icon(
                      selected == p.id
                          ? Icons.radio_button_checked
                          : Icons.radio_button_unchecked,
                      color: selected == p.id
                          ? Theme.of(ctx).colorScheme.primary
                          : null,
                    ),
                    title: Text(p.name),
                    subtitle: Text(
                      p.path,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: p.id == suggestedProjectId
                        ? const Chip(label: Text('自动匹配'))
                        : null,
                    onTap: () => setSheetState(() => selected = p.id),
                  ),
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: () => Navigator.pop(ctx, selected),
                  icon: const Icon(Icons.play_arrow_rounded),
                  label: const Text('确认并接力继续'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  void _showArtifactInfo(DesktopArtifactInfo artifact) {
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(artifact.name),
        content: Text(
          artifact.summary?.isNotEmpty == true
              ? artifact.summary!
              : '该电脑端文档将在接力续聊时自动作为上下文摘要提供给 Agent（大小 ${(artifact.sizeBytes / 1024).toStringAsFixed(1)} KB）。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('知道了'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final detailAsync =
        ref.watch(desktopConversationDetailProvider(widget.conversationId));

    return Scaffold(
      appBar: AppBar(
        title: Text(
          detailAsync.valueOrNull?.summary.title ??
              widget.initialTitle ??
              '电脑端对话',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        actions: [
          IconButton(
            tooltip: '刷新电脑端记录',
            onPressed: () => ref
                .invalidate(desktopConversationDetailProvider(widget.conversationId)),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: detailAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (err, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.error_outline,
                  size: 44,
                  color: Theme.of(context).colorScheme.error,
                ),
                const SizedBox(height: 12),
                Text('加载电脑端对话失败: $err', textAlign: TextAlign.center),
                const SizedBox(height: 16),
                FilledButton.icon(
                  onPressed: () => ref.invalidate(
                    desktopConversationDetailProvider(widget.conversationId),
                  ),
                  icon: const Icon(Icons.refresh),
                  label: const Text('重试'),
                ),
              ],
            ),
          ),
        ),
        data: (detail) {
          final summary = detail.summary;
          final alreadyLinked = summary.linkedSessionId != null;
          return Column(
            children: [
              if (summary.artifacts.isNotEmpty)
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerLow,
                    border: Border(
                      bottom: BorderSide(
                        color: Theme.of(context)
                            .colorScheme
                            .outlineVariant
                            .withValues(alpha: 0.5),
                      ),
                    ),
                  ),
                  child: SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: [
                        const Icon(Icons.description_outlined, size: 16),
                        const SizedBox(width: 6),
                        ...summary.artifacts.map(
                          (art) => Padding(
                            padding: const EdgeInsets.only(right: 6),
                            child: ActionChip(
                              visualDensity: VisualDensity.compact,
                              label: Text(art.name),
                              onPressed: () => _showArtifactInfo(art),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async {
                    ref.invalidate(
                      desktopConversationDetailProvider(widget.conversationId),
                    );
                  },
                  child: ListView.builder(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    itemCount: detail.messages.length,
                    itemBuilder: (_, index) =>
                        MessageBubble(message: detail.messages[index]),
                  ),
                ),
              ),
              SafeArea(
                top: false,
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surface,
                    border: Border(
                      top: BorderSide(
                        color: Theme.of(context)
                            .colorScheme
                            .outlineVariant
                            .withValues(alpha: 0.5),
                      ),
                    ),
                  ),
                  child: FilledButton.icon(
                    onPressed: _continuing ? null : () => _handleContinue(summary),
                    icon: _continuing
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : Icon(
                            alreadyLinked
                                ? Icons.sync_alt_rounded
                                : Icons.play_arrow_rounded,
                          ),
                    label: Text(
                      alreadyLinked
                          ? '继续上次接力（自动同步最新进展）'
                          : '接力继续操作（带电脑上下文）',
                    ),
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}
