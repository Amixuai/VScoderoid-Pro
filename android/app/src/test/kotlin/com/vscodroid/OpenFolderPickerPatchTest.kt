package com.vscodroid

import org.json.JSONObject
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.io.File

/**
 * That patch 0031 offers a command the bundled bridge extension really provides, and
 * only where it should.
 *
 * File > Open Folder, Ctrl+K Ctrl+O and the Explorer's Open Folder button end in the
 * workbench's `pickFolderAndOpen`, which patch 0031 lets ask whether the folder is on
 * the device. "On the device" runs `vscodroid.openFolderFromDevice`. The id sits in a
 * string literal in a TypeScript patch and in the extension's manifest, and nothing
 * between them is compiled. A rename on either side leaves the id unregistered, so the
 * guard that precedes the question hides it and Open Folder quietly goes back to the typed
 * dialog. Reading source rather than running it, for the same reason
 * [ActivityBarItemSizeTest] does: no JVM test can lay out the workbench.
 */
class OpenFolderPickerPatchTest {

    /** The repository root is two levels above `android/app`, this suite's working directory. */
    private val patch: String = File("../../patches/0031-open-folder-offers-the-system-picker.patch").also {
        check(it.isFile) {
            "${it.absolutePath} not found; if the patch was renumbered, point this case at the " +
                "new name rather than letting it pass by looking at nothing"
        }
    }.readText()

    /** The lines the patch adds. Its header prose is never applied, so a pin must not read it. */
    private val addedLines: String =
        patch.lines().filter { it.startsWith("+") && !it.startsWith("+++") }.joinToString("\n")

    /** Found by prefix, because the directory name carries the extension's version. */
    private fun declaredCommands(): Set<String> {
        val root = File("src/main/assets/extensions")
        val matches = root.listFiles { entry: File ->
            entry.isDirectory && entry.name.startsWith("vscodroid.vscodroid-saf-bridge-")
        }?.toList().orEmpty()
        assertEquals(1, matches.size, "expected one bundled SAF bridge extension; found ${matches.map { it.name }}")
        val commands = JSONObject(File(matches.single(), "package.json").readText())
            .getJSONObject("contributes").getJSONArray("commands")
        return (0 until commands.length()).map { commands.getJSONObject(it).getString("command") }.toSet()
    }

    @Test
    fun `the patch runs a command the bridge extension declares`() {
        val id = Regex("""OPEN_FOLDER_FROM_DEVICE = '([\w.]+)';""").find(addedLines)?.groupValues?.get(1)

        assertNotNull(id, "patch 0031 no longer names OPEN_FOLDER_FROM_DEVICE")
        assertTrue(
            id in declaredCommands(),
            "the Open Folder prompt runs `$id`, which the bridge extension does not declare: " +
                "the first button would do nothing. Change both together",
        )
    }

    @Test
    fun `the picker is Android only and runs only a command that is registered`() {
        assertTrue(
            addedLines.contains("isAndroid && !options.forceNewWindow"),
            "the picker must not run off Android, and not for Open in a new window, " +
                "which the picker cannot do",
        )
        assertTrue(
            addedLines.contains("CommandsRegistry.getCommand(OPEN_FOLDER_FROM_DEVICE)"),
            "executeCommand on an unregistered id waits up to 30 seconds for an activation " +
                "event, so the command has to be checked first",
        )
    }

    @Test
    fun `Open Folder starts the picker with no question in front of it`() {
        assertTrue(
            addedLines.contains("await this.commandService.executeCommand(OPEN_FOLDER_FROM_DEVICE);"),
            "Open Folder must run the system picker directly",
        )
        assertTrue(
            !addedLines.contains("dialogService.prompt") && !addedLines.contains("openFolderInApp"),
            "the question that offered the app's private storage is gone for good: the " +
                "workspace is on public storage and every other folder comes from the picker",
        )
    }

    @Test
    fun `a picker that cannot be started falls back to the typed dialog`() {
        assertTrue(
            addedLines.contains("} catch (error) {") && addedLines.contains("return false;"),
            "a command that fails must not leave Open Folder with no dialog at all",
        )
    }
}
