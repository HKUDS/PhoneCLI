package com.phoneagent.app;

import android.content.Context;
import android.graphics.Matrix;
import android.graphics.drawable.Drawable;
import android.util.AttributeSet;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.widget.ImageView;

/**
 * An image you can pinch and drag.
 *
 * The agent's display is 1280x2772; a phone-sized thumbnail of it is not enough
 * to read a row of text or see which button it is about to press. Zooming is
 * the difference between "it is doing something" and "I can see what it sees".
 */
public class ZoomableImage extends ImageView {

    private final Matrix base = new Matrix();
    private final Matrix live = new Matrix();
    private float scale = 1f;
    private float dx = 0f, dy = 0f;

    private ScaleGestureDetector scaleDetector;
    private GestureDetector dragDetector;

    public ZoomableImage(Context context) {
        super(context);
        init(context);
    }

    public ZoomableImage(Context context, AttributeSet attrs) {
        super(context, attrs);
        init(context);
    }

    private void init(Context context) {
        setScaleType(ScaleType.MATRIX);
        scaleDetector = new ScaleGestureDetector(context,
                new ScaleGestureDetector.SimpleOnScaleGestureListener() {
                    @Override
                    public boolean onScale(ScaleGestureDetector detector) {
                        scale = Math.max(0.5f, Math.min(8f, scale * detector.getScaleFactor()));
                        apply();
                        return true;
                    }
                });
        dragDetector = new GestureDetector(context,
                new GestureDetector.SimpleOnGestureListener() {
                    @Override
                    public boolean onScroll(MotionEvent e1, MotionEvent e2, float dX, float dY) {
                        dx -= dX;
                        dy -= dY;
                        apply();
                        return true;
                    }

                    @Override
                    public boolean onDoubleTap(MotionEvent e) {
                        scale = scale > 1.05f ? 1f : 2.5f;
                        if (scale == 1f) { dx = 0f; dy = 0f; }
                        apply();
                        return true;
                    }
                });
    }

    @Override
    public void setImageDrawable(Drawable drawable) {
        super.setImageDrawable(drawable);
        post(this::fit);
    }

    /** Fit the image to the view, then apply the current zoom and pan. */
    private void fit() {
        Drawable d = getDrawable();
        if (d == null || getWidth() == 0 || getHeight() == 0) return;
        float sx = (float) getWidth() / d.getIntrinsicWidth();
        float sy = (float) getHeight() / d.getIntrinsicHeight();
        float s = Math.min(sx, sy);
        base.reset();
        base.postScale(s, s);
        base.postTranslate(
                (getWidth() - d.getIntrinsicWidth() * s) / 2f,
                (getHeight() - d.getIntrinsicHeight() * s) / 2f);
        apply();
    }

    private void apply() {
        live.set(base);
        live.postScale(scale, scale, getWidth() / 2f, getHeight() / 2f);
        live.postTranslate(dx, dy);
        setImageMatrix(live);
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        scaleDetector.onTouchEvent(event);
        dragDetector.onTouchEvent(event);
        if (event.getActionMasked() == MotionEvent.ACTION_UP) {
            // A zoom/pan gesture is still a click as far as talkback is
            // concerned; without this the view is unreachable for anyone using
            // one. It has no click action of its own, so the call is all.
            performClick();
        }
        return true;
    }

    @Override
    public boolean performClick() {
        return super.performClick();
    }

    // Re-fit when the view is laid out (rotation, dialog resize).
    @Override
    protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        super.onSizeChanged(w, h, oldw, oldh);
        fit();
    }
}
