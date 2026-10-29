package com.vscodroid.storage

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Test
import java.io.File

/**
 * How a folder the system picker returns becomes a real path, and that the workspace is
 * named the way the product asked for. The folder-repair and `.nomedia` behaviour sit on
 * `Environment.isExternalStorageManager` and live inotify, which a JVM test cannot stage;
 * the device checklist (SF-28 to SF-30) covers them.
 */
class PublicWorkspaceTest {
    private val shared = File("/storage/emulated/0")

    @Test
    fun `the workspace names are the ones the product asked for`() {
        assertEquals(".VScoderoid-Pro", PublicWorkspace.HIDDEN_NAME)
        assertEquals("VScoderoid-Pro", PublicWorkspace.VISIBLE_NAME)
        assertEquals(".nomedia", PublicWorkspace.NOMEDIA)
    }

    @Test
    fun `a folder on the primary volume is under shared storage`() {
        assertEquals(
            File(shared, "Documents/app"),
            PublicWorkspace.pathForDocumentId("primary:Documents/app", shared),
        )
        assertEquals(
            File(shared, ".VScoderoid-Pro"),
            PublicWorkspace.pathForDocumentId("primary:.VScoderoid-Pro", shared),
        )
    }

    @Test
    fun `the Documents shortcut maps to Documents`() {
        assertEquals(
            File(shared, "Documents/work"),
            PublicWorkspace.pathForDocumentId("home:work", shared),
        )
    }

    @Test
    fun `an SD card or USB drive is its own volume`() {
        assertEquals(
            File("/storage/1A2B-3C4D/projects"),
            PublicWorkspace.pathForDocumentId("1A2B-3C4D:projects", shared),
        )
    }

    @Test
    fun `a volume's top is the volume itself`() {
        assertEquals(shared, PublicWorkspace.pathForDocumentId("primary:", shared))
    }

    @Test
    fun `an id that is not a volume and a path is refused`() {
        assertNull(PublicWorkspace.pathForDocumentId("nonsense", shared))
        assertNull(PublicWorkspace.pathForDocumentId(":x", shared))
    }

    @Test
    fun `a parent segment is refused`() {
        assertNull(PublicWorkspace.pathForDocumentId("primary:Documents/../Android/data", shared))
    }
}
