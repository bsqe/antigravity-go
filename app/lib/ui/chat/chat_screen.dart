import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/chat_state.dart';
import '../../state/connection_state.dart';
import 'widgets/composer_bar.dart';
import 'widgets/message_bubble.dart';

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key, required this.sessionId});

  final String sessionId;

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

  @override
  Widget build(BuildContext context) {
    final chat = ref.watch(chatProvider(widget.sessionId));
    final notifier = ref.read(chatProvider(widget.sessionId).notifier);
    final api = ref.watch(activeConnectionProvider.select((c) => c.httpApi));

    ref.listen<int>(
      chatProvider(widget.sessionId).select(
        (s) => s.messages.isEmpty ? 0 : s.messages.last.content.length + s.messages.last.thoughts.length,
      ),
      (_, __) => _scrollToBottom(),
    );

    return Column(
      children: [
        if (chat.error != null)
          MaterialBanner(
            content: Text(chat.error!),
            actions: [
              TextButton(
                onPressed: () => ref.invalidate(chatProvider(widget.sessionId)),
                child: const Text('重试'),
              ),
            ],
          ),
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
