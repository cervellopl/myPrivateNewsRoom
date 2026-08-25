package pl.myprivatenewsroom.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One news item as served by GET /api/news. */
@Serializable
data class NewsItem(
    val id: Int,
    val title: String,
    /** Publication date, ISO-8601, null when the source did not provide one. */
    val date: String? = null,
    /** Premier (lead) image URL. */
    val image: String? = null,
    /** Human-readable name of the source it came from. */
    val source: String,
    @SerialName("source_id") val sourceId: Int,
    /** Link to the original article. */
    val link: String,
    val summary: String? = null,
    val author: String? = null,
    @SerialName("fetched_at") val fetchedAt: String,
    /** Set when this item is already saved. */
    @SerialName("bookmark_id") val bookmarkId: Int? = null,
    @SerialName("bookmark_category") val bookmarkCategory: String? = null,
) {
    val isSaved: Boolean get() = bookmarkId != null
}

@Serializable
data class BookmarkCategory(
    val id: Int,
    val name: String,
    val color: String? = null,
    @SerialName("bookmark_count") val bookmarkCount: Int = 0,
)

@Serializable
data class CategoryIn(val name: String)

/** A saved item. It carries its own copy of the article, so it outlives the news. */
@Serializable
data class Bookmark(
    val id: Int,
    @SerialName("category_id") val categoryId: Int? = null,
    val category: String? = null,
    @SerialName("news_id") val newsId: Int? = null,
    val title: String,
    val date: String? = null,
    val image: String? = null,
    val source: String? = null,
    val link: String,
    val summary: String? = null,
    val author: String? = null,
    val note: String? = null,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class BookmarkIn(
    @SerialName("news_id") val newsId: Int? = null,
    @SerialName("category_id") val categoryId: Int? = null,
    val note: String? = null,
)

@Serializable
data class BookmarkPatch(
    @SerialName("category_id") val categoryId: Int? = null,
    val note: String? = null,
)

@Serializable
data class NewsPage(
    val total: Int,
    val limit: Int,
    val offset: Int,
    val items: List<NewsItem>,
)

@Serializable
data class Source(
    val id: Int,
    val name: String,
    val plugin: String,
    val enabled: Boolean,
    @SerialName("effective_interval_seconds") val intervalSeconds: Int,
    @SerialName("last_run_at") val lastRunAt: String? = null,
    @SerialName("last_status") val lastStatus: String? = null,
    @SerialName("last_error") val lastError: String? = null,
    @SerialName("news_count") val newsCount: Int = 0,
)

@Serializable
data class Settings(
    @SerialName("poll_interval_seconds") val pollIntervalSeconds: Int,
)

@Serializable
data class RefreshResult(
    val sources: Int = 0,
)

@Serializable
data class Status(
    val version: String,
    @SerialName("poll_interval_seconds") val pollIntervalSeconds: Int,
    @SerialName("auth_required") val authRequired: Boolean,
    val news: Int,
    val sources: Int,
)
