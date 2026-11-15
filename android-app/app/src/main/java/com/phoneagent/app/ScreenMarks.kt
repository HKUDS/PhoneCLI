package com.phoneagent.app

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import java.io.ByteArrayOutputStream
import org.json.JSONArray

/**
 * Set-of-Mark: number the tappable things on a screenshot.
 *
 * The model was asked for a position as a fraction of the screen ("tap 0.42,
 * 0.63"), which is an estimate made from a picture -- two percent off lands on
 * the row above in a dense list. With marks it only has to answer "number 7",
 * and the coordinates come from the accessibility bounds, which are exact.
 *
 * The boxes come from a dump the Python side already took, so this draws and
 * nothing else: no new permission, no second screenshot.
 */
object ScreenMarks {
    /** boxes: [{"i":1,"l":10,"t":20,"r":300,"b":90}, ...] in screen pixels. */
    @JvmStatic
    fun draw(png: ByteArray, boxes: String): ByteArray {
        val bitmap = try {
            BitmapFactory.decodeByteArray(png, 0, png.size)
        } catch (t: Throwable) {
            null
        } ?: return png
        val parsed = try {
            JSONArray(boxes)
        } catch (t: Throwable) {
            return png
        }
        if (parsed.length() == 0) return png

        val marked = bitmap.copy(Bitmap.Config.ARGB_8888, true) ?: return png
        val canvas = Canvas(marked)
        val scale = marked.width / 1280f
        val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = 3f * scale
            color = Color.argb(230, 47, 107, 255)      // the app's accent
        }
        val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            color = Color.argb(255, 47, 107, 255)
        }
        val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textSize = 40f * scale
            isFakeBoldText = true
        }

        for (i in 0 until parsed.length()) {
            val item = parsed.optJSONObject(i) ?: continue
            val number = item.optInt("i", i + 1)
            val rect = RectF(
                item.optInt("l").toFloat(),
                item.optInt("t").toFloat(),
                item.optInt("r").toFloat(),
                item.optInt("b").toFloat(),
            )
            if (rect.width() <= 0f || rect.height() <= 0f) continue
            canvas.drawRoundRect(rect, 8f * scale, 8f * scale, stroke)

            // A number on a tab of its own, so a small box is still readable.
            val label = number.toString()
            val pad = 8f * scale
            val boxWidth = text.measureText(label) + pad * 2
            val boxHeight = text.textSize + pad
            val top = if (rect.top - boxHeight < 0f) rect.top else rect.top - boxHeight
            val tab = RectF(rect.left, top, rect.left + boxWidth, top + boxHeight)
            canvas.drawRoundRect(tab, 6f * scale, 6f * scale, fill)
            canvas.drawText(label, tab.left + pad, tab.bottom - pad * 0.8f, text)
        }

        // Downscale before sending: a 1280x2772 PNG costs about 4.7k tokens
        // (roughly 750 pixels per token, the same figure Mika uses), and the
        // marks are already drawn, so nothing is lost below ~720 wide.
        val target = 720
        val sent = if (marked.width > target) {
            val height = (marked.height.toLong() * target / marked.width).toInt()
            val small = Bitmap.createScaledBitmap(marked, target, height, true)
            if (small !== marked) marked.recycle()
            small
        } else {
            marked
        }
        val out = ByteArrayOutputStream()
        sent.compress(Bitmap.CompressFormat.PNG, 100, out)
        sent.recycle()
        return out.toByteArray()
    }
}
