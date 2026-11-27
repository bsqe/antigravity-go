import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/chat_state.dart';
import '../../state/connection_state.dart';
import 'widgets/composer_bar.dart';
import 'widgets/message_bubble.dart';

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({
    super.key,
    required this.sessionId,
    this.sourceDesktopId,
  });

  final String sessionId;
  final String? sourceDesktopId;

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _scroll = ScrollController();

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (!_scroll.hasClients) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 180),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _syncDesktopNow() async {
    try {
      final added = await ref
          .read(chatProvider(widget.sessionId).notifier)
          .syncFromDesktop();
      if (!mounted) return;
      final text = added > 0 ? '已从电脑端增量同步 $added 条新记录' : '已是最新，电脑端暂无新对话';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
    } catch (err) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('同步电脑端失败: $err')),
      );
    }
  }

  Widget _buildErrorBanner(BuildContext context, String errorText) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
      decoration: BoxDecoration(
        color: scheme.errorContainer.withValues(alpha: 0.7),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: scheme.error.withValues(alpha: 0.35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 110),
            child: SingleChildScrollView(
              child: SelectableText(
                errorText,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: scheme.onErrorContainer,
                    ),
              ),
            ),
          ),
          const SizedBox(height: 4),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(
                onPressed: () => ref.invalidate(chatProvider(widget.sessionId)),
                child: const Text('重试'),
              ),
              TextButton(
                onPressed: () =>
                    ref.read(chatProvider(widget.sessionId).notifier).clearError(),
                child: const Text('关闭'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final chat = ref.watch(chatProvider(widget.sessionId));
    final notifier = ref.read(chatProvider(widget.sessionId).notifier);
    final api = ref.watch(activeConnectionProvider.select((c) => c.httpApi));

    ref.listen<int>(
      chatProvider(widget.sessionId).select(
        (s) => s.messages.isEmpty ? 0 : s.messages.last.content.length + s.messages.last.thoughts.length,
      ),
      (_, _) => _scrollToBottom(),
    );

    return Column(
      children: [
        if (widget.sourceDesktopId != null)
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.secondaryContainer.withValues(alpha: 0.5),
              border: Border(
                bottom: BorderSide(
                  color: Theme.of(context)
                      .colorScheme
                      .outlineVariant
                      .withValues(alpha: 0.5),
                ),
              ),
            ),
            child: Row(
              children: [
                const Icon(Icons.desktop_windows_outlined, size: 16),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    '接力自电脑端对话',
                    style: Theme.of(context).textTheme.labelMedium,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                TextButton.icon(
                  onPressed: chat.syncingDesktop || chat.isGenerating
                      ? null
                      : _syncDesktopNow,
                  icon: chat.syncingDesktop
                      ? const SizedBox(
                          width: 14,
                          height: 14,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.sync, size: 16),
                  label: const Text('同步电脑进展'),
                ),
              ],
            ),
          ),
        if (chat.error != null) _buildErrorBanner(context, chat.error!),
        Expanded(
          child: chat.loading && chat.messages.isEmpty
              ? const Center(child: CircularProgressIndicator())
              : chat.messages.isEmpty
                  ? Center(
                      child: Text(
                        '发送消息或上传报错截图，开始与电脑端 Antigravity 对话',
                        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                              color: Theme.of(context).colorScheme.onSurfaceVariant,
                            ),
                      ),
                    )
                  : ListView.builder(
                      controller: _scroll,
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      itemCount: chat.messages.length + (chat.hasMore ? 1 : 0),
                      itemBuilder: (context, index) {
                        if (chat.hasMore && index == 0) {
                          return Center(
                            child: TextButton(
                              onPressed: notifier.loadOlderMessages,
                              child: const Text('加载更早历史'),
                            ),
                          );
                        }
                        final msg = chat.messages[chat.hasMore ? index - 1 : index];
                        return MessageBubble(message: msg, httpApi: api);
                      },
                    ),
        ),
        ComposerBar(
          isGenerating: chat.isGenerating,
          isUploading: chat.uploadingImages,
          pendingImages: chat.pendingImages,
          onAddImage: notifier.addPendingImage,
          onRemoveImage: notifier.removePendingImageAt,
          onSend: notifier.sendMessage,
          onCancel: notifier.cancelActiveTurn,
        ),
      ],
    );
  }
}
