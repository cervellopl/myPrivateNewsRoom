package pl.myprivatenewsroom.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import android.content.Context
import android.widget.Toast
import pl.myprivatenewsroom.data.Bookmark
import pl.myprivatenewsroom.data.BookmarkCategory
import pl.myprivatenewsroom.data.Exports
import pl.myprivatenewsroom.data.NewsItem
import pl.myprivatenewsroom.data.NewsRepository
import pl.myprivatenewsroom.data.Source

data class FeedState(
    val items: List<NewsItem> = emptyList(),
    val sources: List<Source> = emptyList(),
    val total: Int = 0,
    val loading: Boolean = false,
    val refreshing: Boolean = false,
    val error: String? = null,
    val query: String = "",
    val sourceFilter: Int? = null,
    val serverUrl: String = "",
    val pollIntervalSeconds: Int? = null,
    val categories: List<BookmarkCategory> = emptyList(),
    val bookmarks: List<Bookmark> = emptyList(),
    val categoryFilter: Int? = null,
    val bookmarkQuery: String = "",
    /** Item ids whose export is being rendered right now. */
    val exporting: Set<String> = emptySet(),
) {
    val canLoadMore: Boolean get() = items.size < total
}

class FeedViewModel(app: Application) : AndroidViewModel(app) {

    private val repo = NewsRepository(app)
    private val _state = MutableStateFlow(FeedState())
    val state: StateFlow<FeedState> = _state.asStateFlow()

    init {
        load(reset = true)
    }

    fun load(reset: Boolean = false) = viewModelScope.launch {
        if (_state.value.loading) return@launch
        _state.value = _state.value.copy(loading = true, error = null)
        try {
            val offset = if (reset) 0 else _state.value.items.size
            val page = repo.news(offset, _state.value.query, _state.value.sourceFilter)
            val sources = runCatching { repo.sources() }.getOrDefault(_state.value.sources)
            val categories = runCatching { repo.categories() }
                .getOrDefault(_state.value.categories)
            _state.value = _state.value.copy(
                items = if (reset) page.items else _state.value.items + page.items,
                total = page.total,
                sources = sources,
                categories = categories,
                serverUrl = repo.prefs().serverUrl.first(),
                loading = false,
            )
        } catch (e: Exception) {
            _state.value = _state.value.copy(loading = false, error = friendly(e))
        }
    }

    /** Ask the server to poll every source now, then reload the feed. */
    fun fetchNow() = viewModelScope.launch {
        _state.value = _state.value.copy(refreshing = true, error = null)
        try {
            repo.refreshAll()
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
        _state.value = _state.value.copy(refreshing = false)
        load(reset = true)
    }

    fun setQuery(value: String) {
        _state.value = _state.value.copy(query = value)
        load(reset = true)
    }

    fun setSourceFilter(id: Int?) {
        _state.value = _state.value.copy(sourceFilter = id)
        load(reset = true)
    }

    fun loadPollInterval() = viewModelScope.launch {
        runCatching { repo.settings().pollIntervalSeconds }
            .onSuccess { _state.value = _state.value.copy(pollIntervalSeconds = it) }
    }

    fun setPollInterval(seconds: Int) = viewModelScope.launch {
        try {
            val updated = repo.setPollInterval(seconds)
            _state.value = _state.value.copy(
                pollIntervalSeconds = updated.pollIntervalSeconds, error = null
            )
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
    }

    // --- bookmarks ---------------------------------------------------------

    fun loadBookmarks() = viewModelScope.launch {
        try {
            val categories = repo.categories()
            val bookmarks = repo.bookmarks(_state.value.categoryFilter, _state.value.bookmarkQuery)
            _state.value = _state.value.copy(
                categories = categories, bookmarks = bookmarks, error = null
            )
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
    }

    fun setCategoryFilter(id: Int?) {
        _state.value = _state.value.copy(categoryFilter = id)
        loadBookmarks()
    }

    fun setBookmarkQuery(value: String) {
        _state.value = _state.value.copy(bookmarkQuery = value)
        loadBookmarks()
    }

    /** Save an item, or move it when it is already saved. */
    fun save(item: NewsItem, categoryId: Int?) = viewModelScope.launch {
        try {
            repo.save(item.id, categoryId)
            load(reset = true)
            loadBookmarks()
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
    }

    fun unsave(item: NewsItem) = viewModelScope.launch {
        val id = item.bookmarkId ?: return@launch
        try {
            repo.removeBookmark(id)
            load(reset = true)
            loadBookmarks()
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
    }

    fun removeBookmark(bookmark: Bookmark) = viewModelScope.launch {
        try {
            repo.removeBookmark(bookmark.id)
            loadBookmarks()
            load(reset = true)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
    }

    fun moveBookmark(bookmark: Bookmark, categoryId: Int?) = viewModelScope.launch {
        try {
            repo.moveBookmark(bookmark.id, categoryId)
            loadBookmarks()
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
    }

    fun addCategory(name: String, thenSave: NewsItem? = null) = viewModelScope.launch {
        try {
            val category = repo.createCategory(name)
            _state.value = _state.value.copy(categories = _state.value.categories + category)
            if (thenSave != null) {
                repo.save(thenSave.id, category.id)
                load(reset = true)
            }
            loadBookmarks()
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
    }

    fun deleteCategory(category: BookmarkCategory) = viewModelScope.launch {
        try {
            repo.deleteCategory(category.id)
            loadBookmarks()
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        }
    }

    // --- export --------------------------------------------------------------

    /**
     * Download a full-page render and open it. The first render of a page can
     * take the better part of a minute, so the key stays in [FeedState.exporting]
     * until the file is on disk and the card can show progress.
     */
    fun export(
        context: Context,
        format: String,
        title: String,
        newsId: Int? = null,
        bookmarkId: Int? = null,
    ) = viewModelScope.launch {
        val key = "${newsId ?: "b$bookmarkId"}-$format"
        if (key in _state.value.exporting) return@launch
        _state.value = _state.value.copy(exporting = _state.value.exporting + key)
        Toast.makeText(
            context, "Rendering ${format.uppercase()} — this can take a while", Toast.LENGTH_LONG
        ).show()
        try {
            val body = when {
                newsId != null -> repo.exportNews(newsId, format)
                bookmarkId != null -> repo.exportBookmark(bookmarkId, format)
                else -> return@launch
            }
            val file = Exports.save(context, body, Exports.fileNameFor(title, format))
            Exports.open(context, file)
        } catch (e: Exception) {
            _state.value = _state.value.copy(error = friendly(e))
        } finally {
            _state.value = _state.value.copy(exporting = _state.value.exporting - key)
        }
    }

    fun isExporting(key: String) = key in _state.value.exporting

    fun clearError() {
        _state.value = _state.value.copy(error = null)
    }

    private fun friendly(e: Exception): String = when (e) {
        is java.net.ConnectException, is java.net.UnknownHostException ->
            "Cannot reach the server — check the address in Settings."
        is java.net.SocketTimeoutException -> "The server took too long to answer."
        is retrofit2.HttpException -> when (e.code()) {
            401 -> "Server rejected the token — set it in Settings."
            503 -> "The server cannot render pages — it needs a headless Chromium."
            502 -> "The server failed to render that page."
            else -> "HTTP ${e.code()}"
        }
        else -> e.message ?: e.javaClass.simpleName
    }
}
