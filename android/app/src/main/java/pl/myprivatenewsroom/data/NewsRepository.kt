package pl.myprivatenewsroom.data

import android.content.Context
import kotlinx.coroutines.flow.first

/**
 * Thin repository over [NewsApi]; the client is rebuilt whenever the configured
 * server URL changes so switching servers in Settings takes effect at once.
 */
class NewsRepository(context: Context) {

    private val settings = SettingsStore(context.applicationContext)

    @Volatile private var cachedUrl: String? = null
    @Volatile private var cachedToken: String = ""
    @Volatile private var api: NewsApi? = null

    suspend fun api(): NewsApi {
        val url = settings.serverUrl.first()
        cachedToken = settings.token.first()
        val current = api
        if (current != null && url == cachedUrl) return current
        val fresh = NewsApi.create(url) { cachedToken }
        cachedUrl = url
        api = fresh
        return fresh
    }

    suspend fun news(offset: Int, query: String?, sourceId: Int?, limit: Int = 30): NewsPage =
        api().news(limit = limit, offset = offset, query = query?.ifBlank { null }, sourceId = sourceId)

    suspend fun sources(): List<Source> = api().sources()

    suspend fun refreshAll(): RefreshResult = api().refreshAll()

    suspend fun settings(): Settings = api().settings()

    suspend fun setPollInterval(seconds: Int): Settings =
        api().updateSettings(Settings(seconds))

    suspend fun status(): Status = api().status()

    // --- bookmarks ---------------------------------------------------------

    suspend fun categories(): List<BookmarkCategory> = api().categories()

    suspend fun createCategory(name: String): BookmarkCategory =
        api().createCategory(CategoryIn(name))

    suspend fun deleteCategory(id: Int) = api().deleteCategory(id)

    suspend fun bookmarks(categoryId: Int? = null, query: String? = null): List<Bookmark> =
        api().bookmarks(categoryId, query?.ifBlank { null })

    /** Save a news item; saving one already saved just moves it to [categoryId]. */
    suspend fun save(newsId: Int, categoryId: Int?): Bookmark =
        api().saveBookmark(BookmarkIn(newsId = newsId, categoryId = categoryId))

    suspend fun moveBookmark(id: Int, categoryId: Int?): Bookmark =
        api().updateBookmark(id, BookmarkPatch(categoryId = categoryId))

    suspend fun annotateBookmark(id: Int, note: String): Bookmark =
        api().updateBookmark(id, BookmarkPatch(note = note))

    suspend fun removeBookmark(id: Int) = api().deleteBookmark(id)

    // --- export ------------------------------------------------------------

    suspend fun exportNews(id: Int, format: String) = api().exportNews(id, format)

    suspend fun exportBookmark(id: Int, format: String) = api().exportBookmark(id, format)

    fun prefs(): SettingsStore = settings
}
