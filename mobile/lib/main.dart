import 'package:flutter/material.dart';

void main() => runApp(const VoxisApp());

class VoxisApp extends StatelessWidget {
  const VoxisApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Voxis',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF7357FF)),
          useMaterial3: true,
        ),
        home: const VoxisHome(),
      );
}

class VoxisHome extends StatefulWidget {
  const VoxisHome({super.key});

  @override
  State<VoxisHome> createState() => _VoxisHomeState();
}

class _VoxisHomeState extends State<VoxisHome> {
  final _server = TextEditingController(text: 'http://10.0.2.2:8000');
  final _username = TextEditingController();
  final _message = TextEditingController();
  final List<String> _messages = [];
  bool _connected = false;

  @override
  void dispose() {
    _server.dispose();
    _username.dispose();
    _message.dispose();
    super.dispose();
  }

  void _connect() {
    setState(() => _connected = _username.text.trim().isNotEmpty);
    if (!_connected) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Enter a username first')),
      );
    }
  }

  void _send() {
    final value = _message.text.trim();
    if (value.isEmpty) return;
    setState(() => _messages.add('${_username.text.trim().isEmpty ? "You" : _username.text.trim()}: $value'));
    _message.clear();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: const Row(children: [
            Icon(Icons.forum_rounded),
            SizedBox(width: 10),
            Text('Voxis'),
          ]),
        ),
        body: Column(children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(children: [
              TextField(
                controller: _server,
                decoration: const InputDecoration(
                  labelText: 'Server URL',
                  prefixIcon: Icon(Icons.dns_outlined),
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 10),
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: _username,
                    decoration: const InputDecoration(
                      labelText: 'Username',
                      prefixIcon: Icon(Icons.person_outline),
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton(onPressed: _connect, child: const Text('Join')),
              ]),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerLeft,
                child: Text(_connected ? 'Local demo chat' : 'Not connected',
                    style: TextStyle(color: _connected ? Colors.green : Colors.grey)),
              ),
            ]),
          ),
          const Divider(height: 1),
          Expanded(
            child: _messages.isEmpty
                ? const Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.chat_bubble_outline, size: 52),
                        SizedBox(height: 12),
                        Text('Your conversations will appear here'),
                      ],
                    ),
                  )
                : ListView.builder(
                    padding: const EdgeInsets.all(16),
                    itemCount: _messages.length,
                    itemBuilder: (context, index) => Card(
                      child: Padding(
                        padding: const EdgeInsets.all(12),
                        child: Text(_messages[index]),
                      ),
                    ),
                  ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(children: [
                Expanded(
                  child: TextField(
                    controller: _message,
                    onSubmitted: (_) => _send(),
                    decoration: const InputDecoration(
                      hintText: 'Write a message…',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  onPressed: _send,
                  icon: const Icon(Icons.send_rounded),
                ),
              ]),
            ),
          ),
        ]),
      );
}
