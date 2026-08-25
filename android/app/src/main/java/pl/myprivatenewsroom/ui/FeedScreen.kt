package pl.myprivatenewsroom.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bookmark
import androidx.compose.material.icons.filled.BookmarkBorder
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import pl.myprivatenewsroom.data.NewsItem
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FeedScreen(
    state: FeedState,
    vm: FeedViewModel,
    onOpenSettings: () -> Unit,
    onOpenBookmarks: () -> Unit,
) {
    val context = LocalContext.current
    var searchOpen by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.error) {
        state.error?.let {
            snackbar.showSnackbar(it)
            vm.clearError()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("myPrivateNewsRoom", fontWeight = FontWeight.SemiBold) },
                    actions = {
                        IconButton(onClick = { searchOpen = !searchOpen }) {
                            Icon(Icons.Default.Search, contentDescription = "Search")
                        }
                        IconButton(onClick = onOpenBookmarks) {
                            Icon(Icons.Default.Bookmark, contentDescription = "Bookmarks")
                        }
                        IconButton(onClick = onOpenSettings) {
                            Icon(Icons.Default.Settings, contentDescription = "Settings")
                        }
                    },
                )
                if (searchOpen) {
                    OutlinedTextField(
                        value = state.query,
                        onValueChange = vm::setQuery,
                        placeholder = { Text("Search news…") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                    )
                }
                if (state.sources.isNotEmpty()) {
                    SourceFilterRow(state, vm)
                }
                if (state.loading || state.refreshing) {
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
            }
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = vm::fetchNow,
                icon = { Icon(Icons.Default.Refresh, contentDescription = null) },
                text = { Text(if (state.refreshing) "Fetching…" else "Fetch now") },
            )
        },
    ) { padding ->
        if (state.items.isEmpty() && !state.loading) {
            EmptyState(state, Modifier.padding(padding))
            return@Scaffold
        }

        LazyColumn(
            modifier = Modifier.padding(padding),
            contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            items(state.items, key = { it.id }) { item ->
                NewsCard(item, state, vm) {
                    runCatching {
                        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(item.link)))
                    }
                }
            }
            if (state.canLoadMore) {
                item {
                    TextButton(
                        onClick = { vm.load(reset = false) },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("Load more (${state.items.size} of ${state.total})") }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SourceFilterRow(state: FeedState, vm: FeedViewModel) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        FilterChip(
            selected = state.sourceFilter == null,
            onClick = { vm.setSourceFilter(null) },
            label = { Text("All") },
        )
        var expanded by remember { mutableStateOf(false) }
        val current = state.sources.firstOrNull { it.id == state.sourceFilter }
        Box {
            FilterChip(
                selected = state.sourceFilter != null,
                onClick = { expanded = true },
                label = { Text(current?.name ?: "By source") },
            )
            DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                state.sources.forEach { source ->
                    DropdownMenuItem(
                        text = { Text("${source.name} (${source.newsCount})") },
                        onClick = {
                            vm.setSourceFilter(source.id)
                            expanded = false
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun NewsCard(
    item: NewsItem,
    state: FeedState,
    vm: FeedViewModel,
    onClick: () -> Unit,
) {
    val context = LocalContext.current

    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        shape = RoundedCornerShape(14.dp),
    ) {
        Column {
            if (!item.image.isNullOrBlank()) {
                AsyncImage(
                    model = item.image,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(180.dp)
                        .clip(RoundedCornerShape(topStart = 14.dp, topEnd = 14.dp)),
                )
            }
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    item.title,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
                item.summary?.takeIf { it.isNotBlank() }?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    AssistChip(onClick = onClick, label = { Text(item.source) })
                    Text(
                        item.date?.let { formatDate(it) } ?: "seen ${formatDate(item.fetchedAt)}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.weight(1f))
                    SaveButton(item, state, vm)
                }
                ExportRow(
                    title = item.title,
                    isBusy = { fmt -> state.exporting.contains("${item.id}-$fmt") },
                    onExport = { fmt -> vm.export(context, fmt, item.title, newsId = item.id) },
                )
            }
        }
    }
}

/** Bookmark toggle: picks a category when saving, offers removal when saved. */
@Composable
private fun SaveButton(item: NewsItem, state: FeedState, vm: FeedViewModel) {
    var menuOpen by remember { mutableStateOf(false) }
    var creating by remember { mutableStateOf(false) }

    Box {
        IconButton(onClick = { menuOpen = true }) {
            Icon(
                if (item.isSaved) Icons.Default.Bookmark else Icons.Default.BookmarkBorder,
                contentDescription = if (item.isSaved) "Saved — change or remove" else "Save",
                tint = if (item.isSaved) MaterialTheme.colorScheme.primary
                       else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
            if (item.isSaved) {
                Text(
                    "Saved in ${item.bookmarkCategory ?: "no category"}",
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.padding(start = 12.dp, top = 8.dp, bottom = 4.dp),
                )
            }
            state.categories.forEach { category ->
                DropdownMenuItem(
                    text = { Text(category.name) },
                    trailingIcon = {
                        if (item.bookmarkCategory == category.name) {
                            Icon(Icons.Default.Bookmark, contentDescription = null)
                        }
                    },
                    onClick = { vm.save(item, category.id); menuOpen = false },
                )
            }
            DropdownMenuItem(
                text = { Text("No category") },
                onClick = { vm.save(item, null); menuOpen = false },
            )
            HorizontalDivider()
            DropdownMenuItem(
                text = { Text("New category…") },
                onClick = { menuOpen = false; creating = true },
            )
            if (item.isSaved) {
                DropdownMenuItem(
                    text = { Text("Remove bookmark") },
                    onClick = { vm.unsave(item); menuOpen = false },
                )
            }
        }
    }

    if (creating) {
        var name by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { creating = false },
            title = { Text("New category") },
            text = {
                OutlinedTextField(value = name, onValueChange = { name = it },
                                  singleLine = true, label = { Text("Name") })
            },
            confirmButton = {
                TextButton(
                    enabled = name.isNotBlank(),
                    onClick = { vm.addCategory(name.trim(), thenSave = item); creating = false },
                ) { Text("Create and save") }
            },
            dismissButton = { TextButton(onClick = { creating = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun EmptyState(state: FeedState, modifier: Modifier = Modifier) {
    Column(
        modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("No news yet", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text(
            "Add sources in the web app at ${state.serverUrl.ifBlank { "your server" }}, " +
                "then pull them in with Fetch now.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** Relative for anything under a day, absolute date beyond that. */
private fun formatDate(iso: String?): String {
    if (iso.isNullOrBlank()) return "unknown"
    val instant = runCatching { Instant.parse(iso) }.getOrElse {
        runCatching {
            java.time.OffsetDateTime.parse(iso).toInstant()
        }.getOrNull()
    } ?: return "no date"

    val minutes = Duration.between(instant, Instant.now()).toMinutes()
    return when {
        minutes < 1 -> "just now"
        minutes < 60 -> "$minutes min ago"
        minutes < 1440 -> "${minutes / 60} h ago"
        else -> DateTimeFormatter.ofPattern("d MMM yyyy")
            .withZone(ZoneId.systemDefault())
            .format(instant)
    }
}
