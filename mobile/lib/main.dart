import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

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
  final _password = TextEditingController();
  final _message = TextEditingController();

  bool _registerMode = true;
  bool _busy = false;
  String? _token;
  String? _myUsername;
  String? _peer;
  String _status = 'Not connected';
  List<Map<String, dynamic>> _users = [];
  List<Map<String, dynamic>> _messages = [];

  String get _baseUrl {
    var value = _server.text.trim().replaceAll(RegExp(r'/+$'), '');
    if (!value.startsWith('http://') && !value.startsWith('https://')) {
      value = 'http://$value';
    }
    return value;
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  @override
  void dispose() {
    _server.dispose();
    _username.dispose();
    _password.dispose();
    _message.dispose();
    super.dispose();
  }

  void _showError(Object error) {
    if (!mounted) return;
    final text = error.toString().replaceFirst('Exception: ', '');
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(text)));
  }

  String _apiError(http.Response response) {
    try {
      final data = jsonDecode(response.body);
      final detail = data is Map ? data['detail'] : null;
      if (detail is String) return detail;
      if (detail != null) return detail.toString();
    } catch (_) {}
    return 'HTTP ${response.statusCode}: ${response.reasonPhrase ?? 'request failed'}';
  }

  Future<void> _authenticate() async {
    FocusScope.of(context).unfocus();
    final name = _username.text.trim();
    final password = _password.text;
    if (name.length < 3) {
      _showError('Username must contain at least 3 characters.');
      return;
    }
    if (password.isEmpty) {
      _showError('Enter your password.');
      return;
    }
    if (_registerMode && password.length < 10) {
      _showError('For registration, use a password of at least 10 characters.');
      return;
    }

    setState(() {
      _busy = true;
      _status = 'Connecting…';
    });
    try {
      final response = await http
          .post(
            Uri.parse('$_baseUrl/api/${_registerMode ? 'register' : 'login'}'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({
              'username': name,
              'password': password,
              'device_id': 'android-${name.toLowerCase()}',
              'platform': 'android',
            }),
          )
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(_apiError(response));
      }
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _token = data['token'] as String;
        _myUsername = data['username'] as String;
        _status = 'Connected as ${data['username']}';
        _password.clear();
      });
      await _loadUsers();
    } catch (error) {
      if (mounted) setState(() => _status = 'Connection failed');
      _showError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _loadUsers() async {
    if (_token == null) return;
    try {
      final response = await http
          .get(Uri.parse('$_baseUrl/api/users'), headers: _headers)
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(_apiError(response));
      }
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final users = (data['users'] as List)
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      if (!mounted) return;
      setState(() {
        _users = users;
        if (_peer == null || !users.any((u) => u['username'] == _peer)) {
          _peer = users.isEmpty ? null : users.first['username'] as String;
        }
        _status = users.isEmpty
            ? 'Connected · create another account to start chatting'
            : 'Connected as $_myUsername';
      });
      if (_peer != null) await _loadHistory();
    } catch (error) {
      _showError(error);
    }
  }

  Future<void> _selectPeer(String? peer) async {
    if (peer == null) return;
    setState(() {
      _peer = peer;
      _messages = [];
    });
    await _loadHistory();
  }

  Future<void> _loadHistory() async {
    final peer = _peer;
    if (_token == null || peer == null) return;
    try {
      final response = await http
          .get(Uri.parse('$_baseUrl/api/messages/${Uri.encodeComponent(peer)}'),
              headers: _headers)
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(_apiError(response));
      }
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final messages = (data['messages'] as List)
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      if (mounted && _peer == peer) setState(() => _messages = messages);
    } catch (error) {
      _showError(error);
    }
  }

  Future<void> _sendMessage() async {
    final body = _message.text.trim();
    final peer = _peer;
    if (body.isEmpty || peer == null || _token == null || _busy) return;
    setState(() => _busy = true);
    try {
      final response = await http
          .post(
            Uri.parse('$_baseUrl/api/messages'),
            headers: _headers,
            body: jsonEncode({'recipient': peer, 'body': body}),
          )
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(_apiError(response));
      }
      _message.clear();
      await _loadHistory();
    } catch (error) {
      _showError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _logout() async {
    try {
      if (_token != null) {
        await http.post(Uri.parse('$_baseUrl/api/logout'), headers: _headers);
      }
    } catch (_) {}
    if (!mounted) return;
    setState(() {
      _token = null;
      _myUsername = null;
      _peer = null;
      _users = [];
      _messages = [];
      _status = 'Not connected';
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: const Row(children: [
            Icon(Icons.forum_rounded),
            SizedBox(width: 10),
            Text('Voxis'),
          ]),
          actions: [
            if (_token != null)
              IconButton(
                tooltip: 'Refresh users and history',
                onPressed: _busy ? null : _loadUsers,
                icon: const Icon(Icons.refresh),
              ),
            if (_token != null)
              IconButton(
                tooltip: 'Log out',
                onPressed: _logout,
                icon: const Icon(Icons.logout),
              ),
          ],
        ),
        body: _token == null ? _buildAuth() : _buildChat(),
      );

  Widget _buildAuth() => Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Icon(Icons.forum_rounded, size: 64),
                const SizedBox(height: 12),
                Text(
                  _registerMode ? 'Create your Voxis account' : 'Sign in to Voxis',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 20),
                TextField(
                  controller: _server,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                    labelText: 'API server URL',
                    hintText: 'http://192.168.1.10:8000',
                    prefixIcon: Icon(Icons.dns_outlined),
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _username,
                  textInputAction: TextInputAction.next,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: 'Username',
                    helperText: '3–24 letters, numbers, dot, dash or underscore',
                    prefixIcon: Icon(Icons.person_outline),
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _password,
                  obscureText: true,
                  onSubmitted: (_) => _authenticate(),
                  decoration: InputDecoration(
                    labelText: 'Password',
                    helperText: _registerMode ? 'At least 10 characters' : null,
                    prefixIcon: const Icon(Icons.lock_outline),
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _busy ? null : _authenticate,
                  child: Text(_busy
                      ? 'Please wait…'
                      : _registerMode
                          ? 'Register'
                          : 'Log in'),
                ),
                TextButton(
                  onPressed: _busy
                      ? null
                      : () => setState(() => _registerMode = !_registerMode),
                  child: Text(_registerMode
                      ? 'Already registered? Log in'
                      : 'New to Voxis? Create an account'),
                ),
                const SizedBox(height: 8),
                Text(_status, textAlign: TextAlign.center),
                const SizedBox(height: 8),
                const Text(
                  'Android emulator: use http://10.0.2.2:8000. '
                  'On a real phone, enter your computer’s LAN IP and keep both devices on the same Wi-Fi.',
                  textAlign: TextAlign.center,
                ),
              ],
            ),
          ),
        ),
      );

  Widget _buildChat() => Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
            child: Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String>(
                    value: _peer,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Conversation',
                      border: OutlineInputBorder(),
                      prefixIcon: Icon(Icons.person_outline),
                    ),
                    hint: const Text('Register a second account to chat'),
                    items: _users
                        .map((user) => DropdownMenuItem<String>(
                              value: user['username'] as String,
                              child: Text(
                                '${user['display_name']} (@${user['username']})',
                                overflow: TextOverflow.ellipsis,
                              ),
                            ))
                        .toList(),
                    onChanged: _busy ? null : _selectPeer,
                  ),
                ),
                IconButton(
                  onPressed: _busy ? null : _loadHistory,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
          ),
          Align(
            alignment: Alignment.centerLeft,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: Text(_status, style: Theme.of(context).textTheme.bodySmall),
            ),
          ),
          const Divider(height: 1),
          Expanded(
            child: _messages.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(
                        _peer == null
                            ? 'No other users yet. Create a second account on another device, or log out and register another username.'
                            : 'No messages yet. Send the first message!',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  )
                : ListView.builder(
                    reverse: false,
                    padding: const EdgeInsets.all(12),
                    itemCount: _messages.length,
                    itemBuilder: (context, index) {
                      final item = _messages[index];
                      final mine = item['sender'] == _myUsername;
                      return Align(
                        alignment:
                            mine ? Alignment.centerRight : Alignment.centerLeft,
                        child: Card(
                          color: mine
                              ? Theme.of(context).colorScheme.primaryContainer
                              : Theme.of(context).colorScheme.surfaceContainerHighest,
                          child: Padding(
                            padding: const EdgeInsets.all(12),
                            child: ConstrainedBox(
                              constraints: const BoxConstraints(maxWidth: 300),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    mine ? 'You' : '${item['sender']}',
                                    style: Theme.of(context).textTheme.labelSmall,
                                  ),
                                  const SizedBox(height: 4),
                                  Text('${item['body']}'),
                                  const SizedBox(height: 4),
                                  Text(
                                    '${item['created_at']}',
                                    style: Theme.of(context).textTheme.labelSmall,
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _message,
                      enabled: _peer != null && !_busy,
                      minLines: 1,
                      maxLines: 4,
                      onSubmitted: (_) => _sendMessage(),
                      decoration: const InputDecoration(
                        hintText: 'Write a message…',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    onPressed: _peer == null || _busy ? null : _sendMessage,
                    icon: const Icon(Icons.send_rounded),
                  ),
                ],
              ),
            ),
          ),
        ],
      );
}
