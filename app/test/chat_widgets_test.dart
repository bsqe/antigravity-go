import 'package:agy_client/agy_client.dart';
import 'package:antigravity_go_app/ui/chat/widgets/composer_bar.dart';
import 'package:antigravity_go_app/ui/chat/widgets/message_bubble.dart';
import 'package:antigravity_go_app/ui/chat/widgets/thinking_accordion.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('ThinkingAccordion toggles expanded reasoning text on tap', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: ThinkingAccordion(
            thoughts: 'Step 1: inspect stack trace',
            isStreaming: false,
          ),
        ),
      ),
    );

    expect(find.textContaining('思考过程'), findsOneWidget);
    expect(find.text('Step 1: inspect stack trace'), findsNothing);

    await tester.tap(find.textContaining('思考过程'));
    await tester.pump();
    expect(find.text('Step 1: inspect stack trace'), findsOneWidget);
  });

  testWidgets('MessageBubble renders tool chips and cancelled status', (tester) async {
    const msg = MessageRecord(
      id: 'm1',
      sessionId: 's1',
      turnId: 't1',
      role: 'assistant',
      content: 'Partial answer',
      thoughts: 'Reasoning...',
      toolEvents: [
        ToolEventRecord(id: 'tc1', name: 'view_file', status: 'completed'),
      ],
      status: 'cancelled',
      seq: 2,
      createdAtMs: 1,
      updatedAtMs: 2,
    );

    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: MessageBubble(message: msg)),
      ),
    );

    expect(find.text('view_file'), findsOneWidget);
    expect(find.text('Partial answer'), findsOneWidget);
    expect(find.text('已手动中断生成'), findsOneWidget);
  });

  testWidgets('ComposerBar switches Send button to Stop when generating', (tester) async {
    var cancelled = false;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ComposerBar(
            isGenerating: true,
            isUploading: false,
            pendingImages: const [],
            onAddImage: (_) {},
            onRemoveImage: (_) {},
            onSend: (_) {},
            onCancel: () => cancelled = true,
          ),
        ),
      ),
    );

    expect(find.text('停止'), findsOneWidget);
    await tester.tap(find.text('停止'));
    expect(cancelled, isTrue);
  });
}
