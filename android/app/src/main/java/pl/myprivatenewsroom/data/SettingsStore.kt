package pl.myprivatenewsroom.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore("newsroom")

/** Client-side preferences: which server to talk to, its token, sync cadence. */
class SettingsStore(private val context: Context) {

    private val serverUrlKey = stringPreferencesKey("server_url")
    private val tokenKey = stringPreferencesKey("api_token")
    private val syncMinutesKey = longPreferencesKey("sync_minutes")

    val serverUrl: Flow<String> =
        context.dataStore.data.map { it[serverUrlKey] ?: DEFAULT_SERVER }

    val token: Flow<String> = context.dataStore.data.map { it[tokenKey] ?: "" }

    /** How often the app pulls the server in the background, in minutes. */
    val syncMinutes: Flow<Long> =
        context.dataStore.data.map { it[syncMinutesKey] ?: DEFAULT_SYNC_MINUTES }

    suspend fun setServerUrl(value: String) =
        context.dataStore.edit { it[serverUrlKey] = value.trim() }

    suspend fun setToken(value: String) =
        context.dataStore.edit { it[tokenKey] = value.trim() }

    suspend fun setSyncMinutes(value: Long) =
        context.dataStore.edit { it[syncMinutesKey] = value.coerceAtLeast(15) }

    companion object {
        /** 10.0.2.2 is the host machine as seen from the Android emulator. */
        const val DEFAULT_SERVER = "http://10.0.2.2:8000"
        const val DEFAULT_SYNC_MINUTES = 30L
    }
}
