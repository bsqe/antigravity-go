import 'package:agy_client/agy_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import 'thinking_accordion.dart';

class MessageBubble extends StatelessWidget {
  const MessageBubble({
    super.key,
    required this.message,
    this.httpApi,
  });

  final MessageRecord message;
  final AgyHttpApi? httpApi;

  @override
  Widget build(BuildContext context) {
    final isUser = message.role == 'user';
    final scheme = Theme.of(context).colorScheme;

    if (isUser) {
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 520),
          margin: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: scheme.primaryContainer,
            borderRadius: const BorderRadius.only(
              topLeft: Radius.circular(16),
              topRight: Radius.circular(16),
              bottomLeft: Radius.circular(16),
              bottomRight: Radius.circular(4),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              if (message.attachments.isNotEmpty) _buildAttachments(context),
              if (message.content.isNotEmpty)
                SelectableText(
                  message.content,
                  style: TextStyle(color: scheme.onPrimaryContainer, height: 1.45),
                ),
            ],
          ),
        ),
      );
    }

    final isStreaming = message.status == 'streaming';
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 720),
        margin: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: scheme.surfaceContainerLow,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: scheme.outlineVariant.withValues(alpha: 0.4)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ThinkingAccordion(
              thoughts: message.thoughts,
              isStreaming: isStreaming && message.content.isEmpty,
            ),
            if (message.toolEvents.isNotEmpty) _buildToolStrip(context),
            if (message.content.isNotEmpty)
              MarkdownBody(
                data: message.content,
                selectable: true,
                styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
                  codeblockDecoration: BoxDecoration(
                    color: scheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(8),
                  ),
                ),
              )
            else if (isStreaming && message.thoughts.isEmpty)
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2, color: scheme.primary),
                  ),
                  const SizedBox(width: 8),
                  Text('正在生成…', style: Theme.of(context).textTheme.bodySmall),
                ],
              ),
            if (message.status == 'cancelled')
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  '已手动中断生成',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: scheme.tertiary,
                      ),
                ),
              ),
            if (message.status == 'error')
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  '错误: ${message.errorMessage ?? "执行失败"}',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: scheme.error,
                      ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildToolStrip(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Wrap(
        spacing: 6,
        runSpacing: 6,
        children: message.toolEvents.map((t) {
          final done = t.status == 'completed';
          return Chip(
            visualDensity: VisualDensity.compact,
            avatar: done
                ? Icon(Icons.check_circle_outline, size: 15, color: scheme.primary)
                : SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(strokeWidth: 2, color: scheme.primary),
                  ),
            label: Text(t.name, style: Theme.of(context).textTheme.labelSmall),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildAttachments(BuildContext context) {
    final api = httpApi;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: message.attachments.map((att) {
          if (api == null) {
            return Chip(
              avatar: const Icon(Icons.image_outlined, size: 16),
              label: Text(att.filename),
            );
          }
          return ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: Image.network(
              api.attachmentDownloadUrl(att.id),
              headers: api.authHeaders,
              width: 132,
              height: 132,
              fit: BoxFit.cover,
              errorBuilder: (_, __, ___) => Container(
                width: 132,
                height: 80,
                alignment: Alignment.center,
                color: Theme.of(context).colorScheme.surfaceContainerHighest,
                child: Text(att.filename, overflow: TextOverflow.ellipsis),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}
