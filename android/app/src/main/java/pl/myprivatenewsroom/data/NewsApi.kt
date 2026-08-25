package pl.myprivatenewsroom.data

import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import okhttp3.ResponseBody
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

interface NewsApi {

    @GET("api/news")
    suspend fun news(
        @Query("limit") limit: Int = 30,
        @Query("offset") offset: Int = 0,
        @Query("q") query: String? = null,
        @Query("source_id") sourceId: Int? = null,
        @Query("with_image") withImage: Boolean? = null,
    ): NewsPage

    @GET("api/sources")
    suspend fun sources(): List<Source>

    // --- bookmarks ---------------------------------------------------------

    @GET("api/bookmarks")
    suspend fun bookmarks(
        @Query("category_id") categoryId: Int? = null,
        @Query("q") query: String? = null,
    ): List<Bookmark>

    @POST("api/bookmarks")
    suspend fun saveBookmark(@Body bookmark: BookmarkIn): Bookmark

    @PATCH("api/bookmarks/{id}")
    suspend fun updateBookmark(@Path("id") id: Int, @Body patch: BookmarkPatch): Bookmark

    @DELETE("api/bookmarks/{id}")
    suspend fun deleteBookmark(@Path("id") id: Int)

    @GET("api/bookmark-categories")
    suspend fun categories(): List<BookmarkCategory>

    @POST("api/bookmark-categories")
    suspend fun createCategory(@Body category: CategoryIn): BookmarkCategory

    @DELETE("api/bookmark-categories/{id}")
    suspend fun deleteCategory(@Path("id") id: Int)

    // --- page export -------------------------------------------------------

    /** Rendering a page takes a while, so the body is streamed, not buffered. */
    @Streaming
    @GET("api/news/{id}/export.{fmt}")
    suspend fun exportNews(
        @Path("id") id: Int,
        @Path("fmt") format: String,
    ): ResponseBody

    @Streaming
    @GET("api/bookmarks/{id}/export.{fmt}")
    suspend fun exportBookmark(
        @Path("id") id: Int,
        @Path("fmt") format: String,
    ): ResponseBody

    @POST("api/sources/{id}/refresh")
    suspend fun refreshSource(@Path("id") id: Int): Map<String, kotlinx.serialization.json.JsonElement>

    @POST("api/refresh")
    suspend fun refreshAll(): RefreshResult

    @GET("api/settings")
    suspend fun settings(): Settings

    @PUT("api/settings")
    suspend fun updateSettings(@Body settings: Settings): Settings

    @GET("api/status")
    suspend fun status(): Status

    companion object {
        private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

        /**
         * Build a client for one server. [tokenProvider] is read per request so a
         * token entered in Settings takes effect without rebuilding anything.
         */
        fun create(baseUrl: String, tokenProvider: () -> String): NewsApi {
            val normalized = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
            val client = OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(5, TimeUnit.MINUTES)   // a page render can be slow
                .addInterceptor(Interceptor { chain ->
                    val token = tokenProvider()
                    val request = chain.request().newBuilder().apply {
                        if (token.isNotBlank()) header("X-API-Token", token)
                    }.build()
                    chain.proceed(request)
                })
                .build()

            return Retrofit.Builder()
                .baseUrl(normalized)
                .client(client)
                .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
                .build()
                .create(NewsApi::class.java)
        }
    }
}
