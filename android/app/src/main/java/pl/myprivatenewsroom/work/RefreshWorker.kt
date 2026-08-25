package pl.myprivatenewsroom.work

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import pl.myprivatenewsroom.data.NewsRepository
import java.util.concurrent.TimeUnit

/**
 * Periodically asks the server to poll its sources, so the phone shows fresh
 * news even when the app has not been opened for a while. The authoritative
 * schedule still lives on the server; this only nudges it and warms the cache.
 */
class RefreshWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = try {
        val repo = NewsRepository(applicationContext)
        repo.refreshAll()
        repo.news(offset = 0, query = null, sourceId = null, limit = 1)
        Result.success()
    } catch (e: Exception) {
        if (runAttemptCount < 3) Result.retry() else Result.success()
    }
}

object RefreshScheduler {

    private const val WORK_NAME = "newsroom-refresh"

    fun schedule(context: Context, minutes: Long) {
        val request = PeriodicWorkRequestBuilder<RefreshWorker>(
            minutes.coerceAtLeast(15), TimeUnit.MINUTES
        ).setConstraints(
            Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
        ).build()

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, request
        )
    }
}
