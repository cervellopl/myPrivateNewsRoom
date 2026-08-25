package pl.myprivatenewsroom

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import pl.myprivatenewsroom.data.SettingsStore
import pl.myprivatenewsroom.ui.BookmarksScreen
import pl.myprivatenewsroom.ui.FeedScreen
import pl.myprivatenewsroom.ui.FeedViewModel
import pl.myprivatenewsroom.ui.NewsRoomTheme
import pl.myprivatenewsroom.ui.SettingsScreen
import pl.myprivatenewsroom.work.RefreshScheduler

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val minutes = runBlocking { SettingsStore(this@MainActivity).syncMinutes.first() }
        RefreshScheduler.schedule(this, minutes)

        setContent {
            NewsRoomTheme {
                val nav = rememberNavController()
                val vm: FeedViewModel = viewModel()
                val state by vm.state.collectAsStateWithLifecycle()

                NavHost(navController = nav, startDestination = "feed") {
                    composable("feed") {
                        FeedScreen(
                            state, vm,
                            onOpenSettings = { nav.navigate("settings") },
                            onOpenBookmarks = { nav.navigate("bookmarks") },
                        )
                    }
                    composable("bookmarks") {
                        BookmarksScreen(state, vm, onBack = { nav.popBackStack() })
                    }
                    composable("settings") {
                        SettingsScreen(state, vm, onBack = { nav.popBackStack() })
                    }
                }
            }
        }
    }
}
