package pl.myprivatenewsroom.data

import android.content.Context
import android.content.Intent
import android.os.Environment
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.ResponseBody
import java.io.File

/**
 * Saves an exported page and hands it to another app to open or share.
 *
 * Files go to the app's own external files directory, which needs no storage
 * permission on any supported version, and are shared through a FileProvider
 * so the receiving app gets a grantable content:// URI rather than a file path.
 */
object Exports {

    private const val AUTHORITY_SUFFIX = ".fileprovider"

    fun directory(context: Context): File =
        (context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: context.filesDir)
            .apply { mkdirs() }

    /** A filename that survives contact with a filesystem. */
    fun fileNameFor(title: String, format: String): String {
        val cleaned = title
            .replace(Regex("[^\\p{L}\\p{N}\\s-]"), "")
            .trim()
            .take(70)
            .replace(Regex("\\s+"), "-")
            .ifBlank { "article" }
        return "$cleaned.$format"
    }

    suspend fun save(context: Context, body: ResponseBody, fileName: String): File =
        withContext(Dispatchers.IO) {
            val target = File(directory(context), fileName)
            body.byteStream().use { input ->
                target.outputStream().use { output -> input.copyTo(output) }
            }
            target
        }

    private fun uriFor(context: Context, file: File) =
        FileProvider.getUriForFile(context, context.packageName + AUTHORITY_SUFFIX, file)

    fun open(context: Context, file: File) {
        val mime = if (file.extension == "pdf") "application/pdf" else "image/jpeg"
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uriFor(context, file), mime)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        runCatching { context.startActivity(intent) }
            .onFailure { share(context, file) }   // nothing installed to view it
    }

    fun share(context: Context, file: File) {
        val mime = if (file.extension == "pdf") "application/pdf" else "image/jpeg"
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = mime
            putExtra(Intent.EXTRA_STREAM, uriFor(context, file))
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(
            Intent.createChooser(intent, "Share").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }
}
