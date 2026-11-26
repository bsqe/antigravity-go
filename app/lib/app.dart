import 'package:flutter/material.dart';

import 'ui/shell/home_shell.dart';

class AntigravityGoApp extends StatelessWidget {
  const AntigravityGoApp({super.key});

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xFF4B67B4);
    return MaterialApp(
      title: 'Antigravity Go',
      debugShowCheckedModeBanner: false,
      themeMode: ThemeMode.system,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.light,
        ),
      ),
      darkTheme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: seed,
          brightness: Brightness.dark,
        ),
      ),
      home: const HomeShell(),
    );
  }
}
