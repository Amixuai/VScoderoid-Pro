package com.vscodroid.storage

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.FileObserver
import android.provider.DocumentsContract
import android.provider.Settings
import com.vscodroid.util.Logger
import java.io.File
import java.io.IOException

/**
 * The app's workspace on PUBLIC shared storage: `/storage/emulated/0/.VScoderoid-Pro`.
 *
 * Why this exists: code the user writes must not live in the app's private sandbox
 * (`filesDir`), where Clear Data and an uninstall take it with them and no other app
 * can see it. Everything here assumes the all-files-access permission
 * (`MANAGE_EXTERNAL_STORAGE`), which is what lets the app, and the Node server it
 * runs as the same uid, use real paths on shared storage with no SAF mirror.
 *
 * The folder is deliberately hard to lose by accident:
 *
 *  - The leading dot hides it from file managers and from name searches, so it is
 *    not deleted by a stray tap. Someone has to go and find it.
 *  - `.nomedia` inside it keeps Gallery, Files and other indexers from listing or
 *    scanning its contents.
 *  - `.nomedia` is put back the moment it is deleted ([startGuard]).
 *  - If the user renames the folder to drop the dot, the app notices, and gives the
 *    dot back, but only to a folder that has `.nomedia` in it, so an unrelated folder
 *    that happens to be called `VScoderoid-Pro` is never renamed.
 *
 * Everything that touches disk here must stay off the main thread.
 */
object PublicWorkspace {
    private const val TAG = "PublicWorkspace"

    /** The hidden workspace folder's name. The dot is the point. */
    const val HIDDEN_NAME = ".VScoderoid-Pro"

    /** What the folder is called once someone has removed the dot. */
    const val VISIBLE_NAME = "VScoderoid-Pro"

    const val NOMEDIA = ".nomedia"

    /** The documents provider behind the system folder picker for shared storage. */
    private const val EXTERNAL_STORAGE_AUTHORITY = "com.android.externalstorage.documents"

    /** How often the guard re-checks when no file event arrived. Cheap: a few stats. */
    private const val POLL_MS = 2_000L

    /** The path the product asked for. Used when the platform will not name it. */
    private const val DEFAULT_SHARED_ROOT = "/storage/emulated/0"

    private val lock = Any()

    @Volatile private var guardStarted = false
    private var rootObserver: FileObserver? = null
    private var parentObserver: FileObserver? = null

    // -- Paths --

    /** `/storage/emulated/0`, as the platform spells it on this device. */
    @Suppress("DEPRECATION") // the one call that names shared storage; the app holds all-files access
    fun sharedRoot(): File =
        runCatching { android.os.Environment.getExternalStorageDirectory() }.getOrNull()
            ?: File(DEFAULT_SHARED_ROOT)

    /** `/storage/emulated/0/.VScoderoid-Pro` */
    fun root(): File = File(sharedRoot(), HIDDEN_NAME)

    // -- Permission --

    /**
     * Whether this app may use real paths on shared storage.
     *
     * Answers false rather than throwing wherever the platform call is unavailable
     * (a JVM unit test, or a device older than minSdk), so every caller can treat
     * "no" as "use the old behaviour".
     */
    fun hasAllFilesAccess(): Boolean =
        runCatching { android.os.Environment.isExternalStorageManager() }.getOrDefault(false)

    /**
     * The system screen where the user turns all-files access on for this app, with
     * the general list as a fallback for the few ROMs that lack the per-app page.
     */
    fun allFilesAccessIntents(context: Context): List<Intent> = listOf(
        Intent(
            Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
            Uri.parse("package:${context.packageName}"),
        ),
        Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION),
    )

    // -- Make sure the folder is what it should be --

    /**
     * Brings the folder to its required state, and says whether it is usable:
     *
     *  1. If `.VScoderoid-Pro` is missing but a `VScoderoid-Pro` holding `.nomedia`
     *     is there (the user took the dot off), give the dot back by renaming it.
     *  2. Otherwise create `.VScoderoid-Pro` if it does not exist.
     *  3. Make sure `.nomedia` is inside it.
     *
     * Idempotent, and cheap when there is nothing to do (two stats).
     */
    fun ensure(): Boolean = synchronized(lock) {
        if (!hasAllFilesAccess()) return false
        try {
            restoreHiddenName()
            val root = root()
            if (!root.isDirectory && !root.mkdirs()) {
                Logger.w(TAG, "Could not create the workspace folder")
                return false
            }
            ensureNoMedia(root)
            true
        } catch (e: Exception) {
            Logger.w(TAG, "Could not bring the workspace folder to its required state", e)
            false
        }
    }

    /**
     * The renamed-folder repair.
     *
     * Runs only when the hidden folder is absent. If both exist the dot-less folder
     * is left alone: merging two folders' contents is not something to do on a guess.
     *
     * The test for "this is ours" is `.nomedia` inside, as the product defines it.
     * A folder that was renamed and also had `.nomedia` deleted cannot be recognised
     * this way and is left as it is.
     */
    private fun restoreHiddenName() {
        val hidden = root()
        if (hidden.exists()) return
        val visible = File(sharedRoot(), VISIBLE_NAME)
        if (visible.isDirectory && File(visible, NOMEDIA).exists()) {
            if (visible.renameTo(hidden)) {
                Logger.i(TAG, "The workspace folder had lost its dot; put it back")
            } else {
                Logger.w(TAG, "Could not put the dot back on the workspace folder")
            }
        }
    }

    /** Recreates `.nomedia` if it is gone. Never touches one that exists. */
    private fun ensureNoMedia(root: File) {
        val marker = File(root, NOMEDIA)
        if (marker.exists()) return
        try {
            if (marker.createNewFile()) Logger.i(TAG, "Recreated $NOMEDIA in the workspace folder")
        } catch (e: IOException) {
            // Racing another thread that just made it is fine; anything else is not.
            if (!marker.exists()) Logger.w(TAG, "Could not recreate $NOMEDIA", e)
        }
    }

    // -- The guard --

    /**
     * Starts keeping the folder in its required state for as long as the process
     * lives. Safe to call more than once and from any thread.
     *
     * Two mechanisms, because neither is enough alone. File observers react at once
     * (the "immediately" the product asks for) but inotify on shared storage cannot
     * be relied on for every way another app can change a folder. A two-second poll
     * of a couple of stats catches whatever the observers miss, and is also what
     * starts the guard working after the user grants the permission, with no restart.
     */
    fun startGuard() {
        synchronized(lock) {
            if (guardStarted) return
            guardStarted = true
        }
        Thread({
            while (true) {
                try {
                    guardOnce()
                    Thread.sleep(POLL_MS)
                } catch (_: InterruptedException) {
                    return@Thread
                } catch (e: Exception) {
                    Logger.w(TAG, "Workspace guard pass failed", e)
                    try { Thread.sleep(POLL_MS) } catch (_: InterruptedException) { return@Thread }
                }
            }
        }, "public-workspace-guard").apply { isDaemon = true }.start()
    }

    private fun guardOnce() {
        if (!hasAllFilesAccess()) {
            detachObservers()
            return
        }
        ensure()
        synchronized(lock) {
            if (rootObserver == null || parentObserver == null) attachObservers()
        }
    }

    private fun detachObservers() = synchronized(lock) {
        rootObserver?.stopWatching()
        parentObserver?.stopWatching()
        rootObserver = null
        parentObserver = null
    }

    private fun attachObservers() {
        val root = root()
        val parent = sharedRoot()
        if (!root.isDirectory) return

        // Inside the folder: `.nomedia` deleted or moved away, or the folder itself
        // removed or renamed. A removed folder's watch is dead, so it is rebuilt.
        rootObserver = object : FileObserver(
            root,
            FileObserver.DELETE or FileObserver.MOVED_FROM or
                FileObserver.DELETE_SELF or FileObserver.MOVE_SELF,
        ) {
            override fun onEvent(event: Int, path: String?) {
                val selfGone = event and (FileObserver.DELETE_SELF or FileObserver.MOVE_SELF) != 0
                if (selfGone || path == NOMEDIA) onTamper(rebuild = selfGone)
            }
        }.also { it.startWatching() }

        // Beside it: the folder deleted, renamed (the dot removed) or the dot-less
        // twin appearing. Only the two names that matter reach the repair.
        parentObserver = object : FileObserver(
            parent,
            FileObserver.DELETE or FileObserver.MOVED_FROM or
                FileObserver.MOVED_TO or FileObserver.CREATE,
        ) {
            override fun onEvent(event: Int, path: String?) {
                if (path == HIDDEN_NAME || path == VISIBLE_NAME) onTamper(rebuild = true)
            }
        }.also { it.startWatching() }
    }

    private fun onTamper(rebuild: Boolean) {
        ensure()
        if (rebuild) {
            synchronized(lock) {
                detachObservers()
                attachObservers()
            }
        }
    }

    // -- Folder picker --

    /**
     * Where the system folder picker should start: inside the workspace folder, so
     * the user lands on the app's own folder and can pick it or go anywhere else.
     * Null (the picker's own default) if the platform will not build the address.
     */
    fun initialPickerUri(): Uri? = runCatching {
        DocumentsContract.buildDocumentUri(EXTERNAL_STORAGE_AUTHORITY, "primary:$HIDDEN_NAME")
    }.getOrNull()

    /**
     * The real path of a folder the system picker returned, or null when it cannot
     * be used directly: no all-files access, a provider that is not the device's
     * own storage (Drive, Downloads provider), or a folder that does not exist.
     * Null sends the caller to the older SAF mirror route, which still works.
     */
    fun directFolderFor(treeUri: Uri): File? {
        if (!hasAllFilesAccess()) return null
        if (treeUri.authority != EXTERNAL_STORAGE_AUTHORITY) return null
        val docId = runCatching { DocumentsContract.getTreeDocumentId(treeUri) }.getOrNull()
            ?: return null
        val dir = pathForDocumentId(docId, sharedRoot()) ?: return null
        return dir.takeIf { it.isDirectory }
    }

    /**
     * Turns an external-storage document id into a path.
     *
     *   `primary:Documents/app` -> `<sharedRoot>/Documents/app`
     *   `home:app`              -> `<sharedRoot>/Documents/app`  (the picker's Documents shortcut)
     *   `1A2B-3C4D:work`        -> `/storage/1A2B-3C4D/work`      (SD card or USB drive)
     *
     * Pure, so it is testable without a device. A `..` segment is refused, because
     * a document id is data from another process.
     */
    internal fun pathForDocumentId(docId: String, sharedRoot: File): File? {
        val colon = docId.indexOf(':')
        if (colon <= 0) return null
        val volume = docId.substring(0, colon)
        val relative = docId.substring(colon + 1)
        if (relative.split('/').any { it == ".." }) return null
        val base = when (volume) {
            "primary" -> sharedRoot
            "home" -> File(sharedRoot, "Documents")
            else -> File("/storage/$volume")
        }
        return if (relative.isEmpty()) base else File(base, relative)
    }
}
