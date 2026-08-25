package pl.myprivatenewsroom.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.MoreVert
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
import pl.myprivatenewsroom.data.Bookmark

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BookmarksScreen(state: FeedState, vm: FeedViewModel, onBack: () -> Unit) {
    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    var newCategory by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) { vm.loadBookmarks() }
    LaunchedEffect(state.error) {
        state.error?.let { snackbar.showSnackbar(it); vm.clearError() }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            Column {
                TopAppBar(
                    title = { Text("Bookmarks", fontWeight = FontWeight.SemiBold) },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                        }
                    },
                    actions = {
                        TextButton(onClick = { newCategory = true }) { Text("New category") }
                    },
                )
                OutlinedTextField(
                    value = state.bookmarkQuery,
                    onValueChange = vm::setBookmarkQuery,
                    placeholder = { Text("Search saved items…") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
                )
                Row(
                    Modifier.horizontalScroll(rememberScrollState())
                        .padding(horizontal = 12.dp, vertical = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    FilterChip(
                        selected = state.categoryFilter == null,
                        onClick = { vm.setCategoryFilter(null) },
                        label = { Text("All") },
                    )
                    state.categories.forEach { category ->
                        FilterChip(
                            selected = state.categoryFilter == category.id,
                            onClick = {
                                vm.setCategoryFilter(
                                    if (state.categoryFilter == category.id) null else category.id
                                )
                            },
                            label = { Text("${category.name} ${category.bookmarkCount}") },
                        )
                    }
                }
            }
        },
    ) { padding ->
        if (state.bookmarks.isEmpty()) {
            Column(
                Modifier.padding(padding).fillMaxSize().padding(32.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("Nothing saved yet", style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.height(8.dp))
                Text(
                    "Use the bookmark button on any article in the feed.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            return@Scaffold
        }

        LazyColumn(
            modifier = Modifier.padding(padding),
            contentPadding = PaddingValues(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            items(state.bookmarks, key = { it.id }) { bookmark ->
                BookmarkCard(bookmark, state, vm,
                    onOpen = {
                        runCatching {
                            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(bookmark.link)))
                        }
                    })
            }
        }
    }

    if (newCategory) {
        var name by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { newCategory = false },
            title = { Text("New category") },
            text = {
                OutlinedTextField(
                    value = name, onValueChange = { name = it },
                    singleLine = true, label = { Text("Name") },
                )
            },
            confirmButton = {
                TextButton(
                    enabled = name.isNotBlank(),
                    onClick = { vm.addCategory(name.trim()); newCategory = false },
                ) { Text("Create") }
            },
            dismissButton = { TextButton(onClick = { newCategory = false }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun BookmarkCard(
    bookmark: Bookmark,
    state: FeedState,
    vm: FeedViewModel,
    onOpen: () -> Unit,
) {
    val context = LocalContext.current
    var menuOpen by remember { mutableStateOf(false) }

    Card(Modifier.fillMaxWidth().clickable(onClick = onOpen), shape = RoundedCornerShape(14.dp)) {
        Column {
            if (!bookmark.image.isNullOrBlank()) {
                AsyncImage(
                    model = bookmark.image,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxWidth().height(150.dp)
                        .clip(RoundedCornerShape(topStart = 14.dp, topEnd = 14.dp)),
                )
            }
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(bookmark.title, style = MaterialTheme.typography.titleMedium,
                     maxLines = 3, overflow = TextOverflow.Ellipsis)
                bookmark.note?.takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall,
                         color = MaterialTheme.colorScheme.primary)
                }
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    AssistChip(onClick = { menuOpen = true },
                        label = { Text(bookmark.category ?: "Uncategorised") })
                    bookmark.source?.let {
                        Text(it, style = MaterialTheme.typography.labelMedium,
                             color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Spacer(Modifier.weight(1f))
                    Box {
                        IconButton(onClick = { menuOpen = true }) {
                            Icon(Icons.Default.MoreVert, contentDescription = "More")
                        }
                        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                            Text("Move to", style = MaterialTheme.typography.labelSmall,
                                 modifier = Modifier.padding(start = 12.dp, top = 8.dp))
                            state.categories.forEach { category ->
                                DropdownMenuItem(
                                    text = { Text(category.name) },
                                    onClick = { vm.moveBookmark(bookmark, category.id); menuOpen = false },
                                )
                            }
                            DropdownMenuItem(
                                text = { Text("No category") },
                                onClick = { vm.moveBookmark(bookmark, null); menuOpen = false },
                            )
                            HorizontalDivider()
                            DropdownMenuItem(
                                text = { Text("Remove bookmark") },
                                leadingIcon = { Icon(Icons.Default.Delete, contentDescription = null) },
                                onClick = { vm.removeBookmark(bookmark); menuOpen = false },
                            )
                        }
                    }
                }
                ExportRow(
                    title = bookmark.title,
                    isBusy = { fmt -> state.exporting.contains("b${bookmark.id}-$fmt") },
                    onExport = { fmt ->
                        vm.export(context, fmt, bookmark.title, bookmarkId = bookmark.id)
                    },
                )
            }
        }
    }
}

/** The PDF / JPG pair shown on every card. */
@Composable
fun ExportRow(title: String, isBusy: (String) -> Boolean, onExport: (String) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf("pdf", "jpg").forEach { format ->
            val busy = isBusy(format)
            OutlinedButton(
                onClick = { onExport(format) },
                enabled = !busy,
                contentPadding = PaddingValues(horizontal = 14.dp, vertical = 4.dp),
            ) {
                if (busy) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(14.dp), strokeWidth = 2.dp
                    )
                    Spacer(Modifier.width(8.dp))
                    Text("rendering…")
                } else {
                    Text(format.uppercase())
                }
            }
        }
    }
}
