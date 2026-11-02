package com.vscodroid

import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/**
 * The one address that opens inside the app instead of in a browser.
 *
 * It is asked in two places, the bridge deciding where to send an address and the
 * activity deciding whether to load it, so what counts as the desktop viewer is
 * pinned here once and exactly: a nearby address (another port, another host, the
 * wrong path, https) must not match, or an ordinary dev-server preview would open
 * in this screen instead of the browser it needs.
 */
class DesktopAddressTest {

    @Test
    fun `the address desktop py prints matches`() {
        assertTrue(DesktopAddress.matches("http", "127.0.0.1", 6080, "/vnc.html"))
    }

    @Test
    fun `a dev server preview does not match`() {
        assertFalse(DesktopAddress.matches("http", "127.0.0.1", 3000, "/"))
        assertFalse(DesktopAddress.matches("http", "127.0.0.1", 6080, "/"))
        assertFalse(DesktopAddress.matches("http", "127.0.0.1", 8080, "/vnc.html"))
    }

    @Test
    fun `another host or scheme does not match`() {
        assertFalse(DesktopAddress.matches("http", "localhost", 6080, "/vnc.html"))
        assertFalse(DesktopAddress.matches("http", "192.168.1.5", 6080, "/vnc.html"))
        assertFalse(DesktopAddress.matches("https", "127.0.0.1", 6080, "/vnc.html"))
        assertFalse(DesktopAddress.matches("vscodroid", "127.0.0.1", 6080, "/vnc.html"))
    }

    @Test
    fun `a missing part does not match`() {
        assertFalse(DesktopAddress.matches(null, "127.0.0.1", 6080, "/vnc.html"))
        assertFalse(DesktopAddress.matches("http", null, 6080, "/vnc.html"))
        assertFalse(DesktopAddress.matches("http", "127.0.0.1", -1, "/vnc.html"))
        assertFalse(DesktopAddress.matches("http", "127.0.0.1", 6080, null))
    }
}
