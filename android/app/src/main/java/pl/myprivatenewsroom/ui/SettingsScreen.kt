package pl.myprivatenewsroom.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardOptions
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import pl.myprivatenewsroom.data.SettingsStore
import pl.myprivatenewsroom.work.RefreshScheduler

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(state: FeedState, vm: FeedViewModel, onBack: () -> Unit) {
    val context = LocalContext.current
    val store = remember { SettingsStore(context) }
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }

    var serverUrl by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var syncMinutes by remember { mutableStateOf("") }
    var serverInterval by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        serverUrl = store.serverUrl.first()
        token = store.token.first()
        syncMinutes = store.syncMinutes.first().toString()
        vm.loadPollInterval()
    }
    LaunchedEffect(state.pollIntervalSeconds) {
        state.pollIntervalSeconds?.let { serverInterval = it.toString() }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            Modifier.padding(padding).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            Section("Server") {
                OutlinedTextField(
                    value = serverUrl,
                    onValueChange = { serverUrl = it },
                    label = { Text("Base URL") },
                    supportingText = { Text("e.g. http://192.168.1.50:8000 — use 10.0.2.2 on the emulator") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = token,
                    onValueChange = { token = it },
                    label = { Text("X-API-Token (optional)") },
                    visualTransformation = PasswordVisualTransformation(),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = {
                        scope.launch {
                            store.setServerUrl(serverUrl)
                            store.setToken(token)
                            vm.load(reset = true)
                            vm.loadPollInterval()
                            snackbar.showSnackbar("Server saved")
                        }
                    },
                ) { Text("Save server") }
            }

            Section("How often the server fetches news") {
                OutlinedTextField(
                    value = serverInterval,
                    onValueChange = { serverInterval = it.filter(Char::isDigit) },
                    label = { Text("Seconds between polls") },
                    supportingText = { Text("Applies to every source on the server (minimum 60).") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(300 to "5 min", 900 to "15 min", 3600 to "1 h").forEach { (value, label) ->
                        AssistChip(onClick = { serverInterval = value.toString() }, label = { Text(label) })
                    }
                }
                Button(
                    enabled = serverInterval.toIntOrNull() != null,
                    onClick = {
                        vm.setPollInterval(serverInterval.toInt())
                        scope.launch { snackbar.showSnackbar("Server polling interval updated") }
                    },
                ) { Text("Apply on server") }
            }

            Section("How often this phone syncs") {
                OutlinedTextField(
                    value = syncMinutes,
                    onValueChange = { syncMinutes = it.filter(Char::isDigit) },
                    label = { Text("Minutes between background syncs") },
                    supportingText = { Text("Android enforces a 15 minute minimum.") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    enabled = syncMinutes.toLongOrNull() != null,
                    onClick = {
                        scope.launch {
                            val minutes = syncMinutes.toLong().coerceAtLeast(15)
                            store.setSyncMinutes(minutes)
                            syncMinutes = minutes.toString()
                            RefreshScheduler.schedule(context, minutes)
                            snackbar.showSnackbar("Background sync every $minutes min")
                        }
                    },
                ) { Text("Save sync interval") }
            }

            Section("Status") {
                Text(
                    "Sources: ${state.sources.size} · items loaded: ${state.items.size} of ${state.total}",
                    style = MaterialTheme.typography.bodyMedium,
                )
                state.sources.forEach { source ->
                    ListItem(
                        headlineContent = { Text(source.name) },
                        supportingContent = {
                            Text(
                                "${source.plugin} · every ${source.intervalSeconds}s · " +
                                    (source.lastStatus ?: "never run") +
                                    (source.lastError?.let { " — $it" } ?: "")
                            )
                        },
                        trailingContent = { Text("${source.newsCount}") },
                    )
                }
            }
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}
