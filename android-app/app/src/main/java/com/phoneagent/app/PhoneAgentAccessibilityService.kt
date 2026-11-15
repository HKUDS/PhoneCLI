package com.phoneagent.app

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo

/**
 * Reads the control tree of the virtual display.
 *
 * The virtual display is created by the shell helper, so its windows belong to
 * other apps. Only an accessibility service may look inside them, and only it
 * can say what a coordinate means -- which is the difference between "tap
 * 640,1661" and "tap the row labelled 无线调试".
 *
 * Gestures are still dispatched through `input -d <id>`, because
 * [dispatchGesture] has no display argument and always targets the default
 * display. Reading here, acting through the shell.
 */
class PhoneAgentAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i("Pairing", "accessibility service connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // read on demand: the agent asks when it needs the tree
    }

    override fun onInterrupt() {}

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "Pairing"
        private const val MAX_NODES = 4000

        @Volatile
        var instance: PhoneAgentAccessibilityService? = null
            private set

        private val dumpPool = java.util.concurrent.Executors.newCachedThreadPool()
        private val dumps = java.util.concurrent.ConcurrentHashMap<Int, java.util.concurrent.Future<String>>()
        private val dumpSeq = java.util.concurrent.atomic.AtomicInteger(0)
        private const val MAX_PENDING_DUMPS = 8

        /**
         * Start a dump on a worker thread and return a handle to poll.
         *
         * dump() crosses into system_server twice per window, and that call
         * can block for as long as system_server is busy -- during an activity
         * relaunch it does. Called synchronously from Python it froze the
         * whole interpreter: the HTTP server kept its socket but stopped
         * answering, and the task stayed "running" forever. Polling instead of
         * waiting means a busy system_server can only delay one dump.
         */
        @JvmStatic
        fun beginDump(displayId: Int): Int {
            if (dumps.size >= MAX_PENDING_DUMPS) {
                dumps.entries.removeIf { it.value.isDone }
            }
            val tag = dumpSeq.incrementAndGet()
            dumps[tag] = dumpPool.submit(java.util.concurrent.Callable { dump(displayId) })
            return tag
        }

        /** The finished tree for [tag], or "" while it is still being built. */
        @JvmStatic
        fun pollDump(tag: Int): String {
            val future = dumps[tag] ?: return "<error>unknown dump $tag</error>"
            if (!future.isDone) return ""
            dumps.remove(tag)
            return try {
                future.get()
            } catch (t: Throwable) {
                "<error>dump failed: ${t.message}</error>"
            }
        }

        private val actions = java.util.concurrent.ConcurrentHashMap<Int, java.util.concurrent.Future<Boolean>>()
        private val actionSeq = java.util.concurrent.atomic.AtomicInteger(0)

        private fun startAction(block: () -> Boolean): Int {
            if (actions.size >= MAX_PENDING_DUMPS) {
                actions.entries.removeIf { it.value.isDone }
            }
            val tag = actionSeq.incrementAndGet()
            actions[tag] = dumpPool.submit(java.util.concurrent.Callable { block() })
            return tag
        }

        @JvmStatic
        fun beginClick(displayId: Int, text: String): Int = startAction { clickText(displayId, text) }

        @JvmStatic
        fun beginScroll(displayId: Int, forward: Boolean): Int = startAction { scroll(displayId, forward) }

        @JvmStatic
        fun beginSetText(displayId: Int, value: String): Int = startAction { setText(displayId, value) }

        /** -1 while [tag] is still running, else 1 for true and 0 for false. */
        @JvmStatic
        fun pollAction(tag: Int): Int {
            val future = actions[tag] ?: return 0
            if (!future.isDone) return -1
            actions.remove(tag)
            return try {
                if (future.get()) 1 else 0
            } catch (t: Throwable) {
                0
            }
        }

        /**
         * The window tree of [displayId] as XML.
         *
         * Returns an `<error>` element rather than throwing, because the
         * caller is Python across the Chaquopy bridge and an exception there
         * would surface as an opaque transport failure.
         */
        @JvmStatic
        fun dump(displayId: Int): String {
            val service = instance ?: return "<error>accessibility service is not enabled</error>"
            val byDisplay = try {
                service.windowsOnAllDisplays
            } catch (t: Throwable) {
                return "<error>cannot list windows: ${t.message}</error>"
            }
            val windows = byDisplay.get(displayId)
                ?: return "<error>no windows on display $displayId " +
                    "(present: ${(0 until byDisplay.size()).joinToString(",") { byDisplay.keyAt(it).toString() }})</error>"

            val out = StringBuilder()
            out.append("<hierarchy display=\"").append(displayId)
                .append("\" windows=\"").append(windows.size).append("\">\n")
            var budget = MAX_NODES
            // The focused window first. On the phone's own screen the launcher
            // keeps a window around behind the app, and whichever came first in
            // the list decided what "the foreground app" looked like -- a
            // hands-off crawl kept reading com.miui.home while the app it had
            // just launched was plainly on top.
            val ordered = windows.sortedByDescending { if (it.isFocused) 2 else if (it.isActive) 1 else 0 }
            for (window in ordered) {
                val root = window.root ?: continue
                try {
                    budget = appendNode(out, root, 1, budget)
                } finally {
                    root.recycle()
                }
            }
            out.append("</hierarchy>")
            return out.toString()
        }

        /**
         * Scrolls the first scrollable node on the display.
         *
         * `dispatchGesture` cannot target a virtual display, but
         * ACTION_SCROLL_FORWARD/BACKWARD acts on the node like a click does,
         * so scrolling stays exact instead of becoming a blind swipe.
         */
        @JvmStatic
        fun scroll(displayId: Int, forward: Boolean): Boolean {
            val service = instance ?: return false
            val windows = try {
                service.windowsOnAllDisplays.get(displayId)
            } catch (t: Throwable) {
                return false
            } ?: return false

            val action = if (forward) {
                AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            } else {
                AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            }
            for (window in windows) {
                val root = window.root ?: continue
                var node: AccessibilityNodeInfo? = null
                try {
                    node = findScrollable(root, 0)
                    if (node != null) {
                        val ok = node.performAction(action)
                        Log.i(TAG, "scroll(forward=$forward): $ok on ${node.viewIdResourceName}")
                        if (ok) return true
                    }
                } finally {
                    node?.recycle()
                    root.recycle()
                }
            }
            return false
        }

        private fun findScrollable(node: AccessibilityNodeInfo, depth: Int): AccessibilityNodeInfo? {
            if (depth > 40) return null
            if (node.isScrollable) return AccessibilityNodeInfo.obtain(node)
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                val found = try {
                    findScrollable(child, depth + 1)
                } finally {
                    child.recycle()
                }
                if (found != null) return found
            }
            return null
        }

        /**
         * Clicks the control whose text or description contains [needle].
         *
         * This is the accessibility action, not a coordinate: it acts on the
         * node itself, so it needs no display id, survives layout shifts and
         * reports whether the action was accepted. [tapText] falls back to
         * coordinates only when this returns false.
         */
        @JvmStatic
        fun clickText(displayId: Int, needle: String): Boolean {
            val service = instance ?: return false
            val windows = try {
                service.windowsOnAllDisplays.get(displayId)
            } catch (t: Throwable) {
                return false
            } ?: return false

            for (window in windows) {
                val root = window.root ?: continue
                var match: AccessibilityNodeInfo? = null
                try {
                    match = findNode(root, needle, 0)
                    if (match != null) {
                        val target = clickableAncestor(match)
                        val ok = target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                        Log.i(TAG, "clickText(${needle}): $ok on ${target.viewIdResourceName}")
                        if (target !== match) target.recycle()
                        return ok
                    }
                } finally {
                    match?.recycle()
                    root.recycle()
                }
            }
            return false
        }

        /**
         * Types into the first editable field on the display.
         *
         * ``ACTION_SET_TEXT`` writes the value in one step instead of replaying
         * keystrokes through the shell, which is both faster and immune to the
         * IME swallowing characters.
         */
        @JvmStatic
        fun setText(displayId: Int, value: String): Boolean {
            val service = instance ?: return false
            val windows = try {
                service.windowsOnAllDisplays.get(displayId)
            } catch (t: Throwable) {
                return false
            } ?: return false

            for (window in windows) {
                val root = window.root ?: continue
                var field: AccessibilityNodeInfo? = null
                try {
                    field = findEditable(root, 0)
                    if (field != null) {
                        val args = android.os.Bundle().apply {
                            putCharSequence(
                                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                                value,
                            )
                        }
                        val ok = field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
                        Log.i(TAG, "setText(${value}): $ok on ${field.viewIdResourceName}")
                        return ok
                    }
                } finally {
                    field?.recycle()
                    root.recycle()
                }
            }
            return false
        }

        private fun findNode(node: AccessibilityNodeInfo, needle: String, depth: Int): AccessibilityNodeInfo? {
            if (depth > 40) return null
            val label = (node.text?.toString() ?: "") + " " + (node.contentDescription?.toString() ?: "")
            if (label.contains(needle)) return AccessibilityNodeInfo.obtain(node)
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                val found = try {
                    findNode(child, needle, depth + 1)
                } finally {
                    child.recycle()
                }
                if (found != null) return found
            }
            return null
        }

        private fun findEditable(node: AccessibilityNodeInfo, depth: Int): AccessibilityNodeInfo? {
            if (depth > 40) return null
            if (node.isEditable || node.className?.toString()?.contains("EditText") == true) {
                return AccessibilityNodeInfo.obtain(node)
            }
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                val found = try {
                    findEditable(child, depth + 1)
                } finally {
                    child.recycle()
                }
                if (found != null) return found
            }
            return null
        }

        /** A label is usually not the clickable node; its row is. */
        private fun clickableAncestor(node: AccessibilityNodeInfo): AccessibilityNodeInfo {
            var current = node
            for (step in 0 until 8) {
                if (current.isClickable) break
                val parent = current.getParent() ?: break
                if (current !== node) current.recycle()
                current = parent
            }
            return current
        }

        /**
         * A swipe through `dispatchGesture`.
         *
         * Whether this lands on the virtual display or on the default one is
         * exactly what the caller is testing: the API takes no display id, so
         * the answer decides whether gestures can be accessibility-native here.
         */
        @JvmStatic
        fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Long = 300): Boolean {
            val service = instance ?: return false
            val callback = object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(gestureDescription: android.accessibilityservice.GestureDescription?) {
                    Log.i(TAG, "swipe completed")
                }

                override fun onCancelled(gestureDescription: android.accessibilityservice.GestureDescription?) {
                    Log.i(TAG, "swipe cancelled")
                }
            }
            val path = android.graphics.Path().apply { moveTo(x1.toFloat(), y1.toFloat()) }
            if (x2 == x1 && y2 == y1) {
                // unchanged
            }
            path.lineTo(x2.toFloat(), y2.toFloat())
            val stroke = android.accessibilityservice.GestureDescription.StrokeDescription(
                path, 0L, durationMs
            )
            val gesture = android.accessibilityservice.GestureDescription.Builder()
                .addStroke(stroke)
                .build()
            val accepted = service.dispatchGesture(gesture, callback, null)
            Log.i(TAG, "swipe ($x1,$y1)->($x2,$y2) accepted=$accepted")
            return accepted
        }

        /**
         * Reads the wireless-debugging pairing dialog: ``code@host:port``.
         *
         * Recognises the text by its *shape*, not by its language or its view
         * ids (the approach Mika's PairingScreen uses): the code is a whole
         * text node that is exactly six digits, the address is an
         * ``IPv4:port``. A window showing two different codes, or two
         * different addresses, is treated as unread -- better to fall back than
         * to pair with the wrong digits.
         *
         * Only windows belonging to the system settings package are considered:
         * the page underneath the dialog also carries an ``IP:port`` line.
         */
        @JvmStatic
        fun scanPairingDialog(): String {
            val service = instance ?: return ""
            val settings = settingsPackage(service) ?: return ""
            val windows = try {
                service.windowsOnAllDisplays.get(0) ?: return ""
            } catch (t: Throwable) {
                Log.i(TAG, "scanPairingDialog: ${t.message}")
                return ""
            }

            for (window in windows) {
                val root = window.root ?: continue
                try {
                    if (root.packageName?.toString() != settings) continue
                    val texts = StringBuilder()
                    collectTexts(root, texts, 0)
                    val lines = texts.lines().filter { it.isNotBlank() }
                    val code = lines
                        .map { line -> line.filterNot { it.isWhitespace() } }
                        .filter { CODE.matches(it) }
                        .toSet()
                        .singleOrNull() ?: continue
                    val address = lines.flatMap(::addresses).toSet().singleOrNull() ?: continue
                    Log.i(TAG, "pairing dialog read: code of ${code.length} digits, $address")
                    return "$code@$address"
                } finally {
                    root.recycle()
                }
            }
            return ""
        }

        private val CODE = Regex("""\d{6}""")
        private val IPV4_PORT =
            Regex("""(?<![\w.:])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3}):(\d{1,5})(?![\w.:])""")
        private val IPV6_PORT =
            Regex("""\[[0-9A-Fa-f]*:[0-9A-Fa-f:.]*(?:%[\w.]+)?]:(\d{1,5})(?![\w.:])""")

        /** ``host:port`` strings with sane octets and a real port range. */
        private fun addresses(text: String): List<String> {
            val v4 = IPV4_PORT.findAll(text).filter { match ->
                (1..4).all { match.groupValues[it].toInt() <= 255 }
            }.map { match -> "${match.groupValues[1]}.${match.groupValues[2]}.${match.groupValues[3]}." +
                "${match.groupValues[4]}:${match.groupValues[5]}" }
            return v4.filter { match ->
                val port = match.substringAfterLast(':').toIntOrNull()
                port != null && port in 1..65535
            }.toList()
        }

        /** The package that owns the system settings UI; not always com.android.settings. */
        private fun settingsPackage(service: PhoneAgentAccessibilityService): String? {
            val intent = android.content.Intent(android.provider.Settings.ACTION_SETTINGS)
            val resolved = service.packageManager.resolveActivity(intent, 0)
            return resolved?.activityInfo?.packageName ?: "com.android.settings"
        }

        private fun collectTexts(node: AccessibilityNodeInfo, out: StringBuilder, depth: Int) {
            if (depth > 40) return
            node.text?.toString()?.let { if (it.isNotBlank()) out.append(it).append('\n') }
            node.contentDescription?.toString()?.let {
                if (it.isNotBlank()) out.append(it).append('\n')
            }
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                try {
                    collectTexts(child, out, depth + 1)
                } finally {
                    child.recycle()
                }
            }
        }

        private fun appendNode(
            out: StringBuilder,
            node: AccessibilityNodeInfo,
            depth: Int,
            budget: Int,
        ): Int {
            if (budget <= 0) return 0
            val bounds = Rect()
            node.getBoundsInScreen(bounds)
            val text = node.text?.toString()?.replace("<", "&lt;")?.replace(">", "&gt;")
            val hint = node.hintText?.toString()?.replace("<", "&lt;")?.replace(">", "&gt;")
            val desc = node.contentDescription?.toString()?.replace("<", "&lt;")?.replace(">", "&gt;")
            val id = node.viewIdResourceName ?: ""
            val cls = node.className?.toString()?.substringAfterLast('.') ?: ""

            val pkg = node.packageName?.toString()?.replace("<", "&lt;") ?: ""

            out.append("  ".repeat(depth)).append("<node class=\"").append(cls)
                // uiautomator's format on purpose: the agent's label parser and
                // anything else that reads a dump expect [l,t][r,b]
                .append("\" bounds=\"[").append(bounds.left).append(",").append(bounds.top)
                .append("][").append(bounds.right).append(",").append(bounds.bottom).append("]\"")
            // The crawler's start check reads this attribute to decide whether
            // the screen it dumped is the app it is about to crawl; leaving it
            // out made every check read "the dump is not the app".
            if (pkg.isNotEmpty()) out.append(" package=\"").append(pkg).append("\"")
            if (!text.isNullOrBlank()) out.append(" text=\"").append(text).append("\"")
            if (!desc.isNullOrBlank()) out.append(" content-desc=\"").append(desc).append("\"")
            if (!hint.isNullOrBlank()) out.append(" hint=\"").append(hint).append("\"")
            if (id.isNotEmpty()) out.append(" id=\"").append(id.substringAfterLast('/')).append("\"")
            if (node.isClickable) out.append(" clickable=\"true\"")
            if (node.isEditable) out.append(" editable=\"true\"")
            if (node.isScrollable) out.append(" scrollable=\"true\"")
            if (node.isCheckable) out.append(" checkable=\"true\"")
            if (node.isChecked) out.append(" checked=\"true\"")
            // A tab's state lives in isSelected, not isChecked. Without it every
            // tab of a feed looks identical to the crawler, which is why 小红书
            // produced two screens for six tabs.
            if (node.isSelected) out.append(" selected=\"true\"")
            // Recorded for debugging and for "is the field ready to type into",
            // but deliberately not part of a screen's identity: focus comes and
            // goes on the same page, and putting it in the fingerprint would make
            // one page look like two.
            if (node.isFocused) out.append(" focused=\"true\"")
            if (node.isEnabled) out.append(" enabled=\"true\"")

            val children = node.childCount
            if (children == 0) {
                out.append("/>\n")
                return budget - 1
            }
            out.append(">\n")
            var left = budget - 1
            for (i in 0 until children) {
                val child = node.getChild(i) ?: continue
                if (left <= 0) {
                    child.recycle()
                    continue
                }
                left = appendNode(out, child, depth + 1, left)
                child.recycle()
            }
            out.append("  ".repeat(depth)).append("</node>\n")
            return left
        }
    }
}
