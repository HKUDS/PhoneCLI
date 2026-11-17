package com.phoneagent.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.text.InputType;
import android.widget.ArrayAdapter;
import android.widget.Spinner;
import android.net.Uri;
import android.os.PowerManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * Chat surface for the agent that runs inside this app.
 *
 * `AgentService` owns the virtual display and serves tasks on loopback, so the
 * phone needs neither a host process nor `adb reverse`: this activity posts to
 * 127.0.0.1 and polls for steps and the latest virtual-display frame.
 */
public class MainActivity extends Activity {

    private static final String BASE = "http://127.0.0.1:8765";
    //: Set from filesDir/agent-token in onCreate. The agent rejects requests
    //: without it: on Android any app can reach loopback, so an open port here
    //: would let anything on the phone drive the agent.
    private static String TOKEN = "";

    private static HttpURLConnection connect(String path) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(BASE + path).openConnection();
        if (!TOKEN.isEmpty()) conn.setRequestProperty("X-Agent-Token", TOKEN);
        return conn;
    }

    private android.view.ViewGroup messages;
    private android.view.View welcome;
    private android.widget.LinearLayout lastAgentBubble;
    private EditText input;
    private ScrollView scroll;
    private ImageView frameView;
    private android.view.View frameCard;
    //: The in-app mirror of the agent's screen (see the layout's comment).
    private ImageView capsuleFrame;
    private android.view.View capsule;
    private android.widget.TextView capsuleDot;
    private boolean capsuleCollapsed;
    //: Only a capsule with a picture is worth showing; an empty one looks broken.
    private boolean hasFrame;
    private TextView frameLabel;
    private android.app.Dialog frameDialog;
    private ZoomableImage bigFrame;
    private View statusRow;
    private TextView statusText;
    private android.view.View sendButton;
    // which task the cancel button should stop, and how much of its step list
    // has already been shown
    private String currentTaskId = "";
    //: Is a poll chain alive? MIUI's power keeper froze this process mid-task
    //: and the chain died with it, leaving "执行中…" on screen forever.
    private boolean polling = false;
    //: What the interpreter reported at startup; shown in 设置 → 诊断.
    private String bootInfo = "";
    //: When healAgent last ran; the check must not become a reconnect loop.
    private long lastHealAt = 0;
    //: Retries spent waiting for a just-started agent to become ready.
    private int healAttempts;
    //: The app a running crawl is building a map of, for its messages.
    private String crawlName = "";
    //: One worker for the polling loops. A task polls every 0.7 s and the frame
    //: ticker every 2.5 s; a thread each time was dozens of threads a minute for
    //: work that is strictly sequential.
    private final java.util.concurrent.ExecutorService io =
            java.util.concurrent.Executors.newFixedThreadPool(2, runnable -> {
                Thread thread = new Thread(runnable, "pa-io");
                thread.setDaemon(true);
                return thread;
            });
    //: The one view that accumulates step lines, and how many bubbles to keep.
    private TextView stepBlock;
    //: The step lines as text, so the highlighted ones survive a rebuild.
    private final java.util.ArrayList<String> stepLines = new java.util.ArrayList<>();
    private static final int MAX_BUBBLES = 120;
    //: When the poll chain last heard from the agent. A chain can die without
    //: saying so (an Error kills the thread past `catch (Exception)`), and the
    //: screen then says "执行中…" forever.
    private volatile long lastPollAt = 0;
    //: The task whose answer is already on screen. The watchdog can restart a
    //: chain for a task that has just finished, and without this the same
    //: completion was printed again every three seconds.
    private String answeredTaskId = "";
    //: Frames are full screenshots; decoding every poll is what filled the heap.
    private long lastFrameAt = 0;

    /**
     * Keeps the screen honest even when the poll chain dies.
     *
     * Three times now a task finished on the phone while the chat still said
     * "执行中…": the polling thread was gone and nothing was left to notice.
     * A Handler on the main looper survives that, so it re-arms the chain.
     */
    private final Runnable pollWatchdog = new Runnable() {
        @Override
        public void run() {
            if (!currentTaskId.isEmpty()
                    && System.currentTimeMillis() - lastPollAt > 5000) {
                polling = false;
                poll(currentTaskId, 0);
            }
            handler.postDelayed(this, 3000);
        }
    };
    // set while the agent is waiting for an answer: the next 发送 goes to it
    private String awaitingTaskId = "";
    private int shownSteps = 0;
    private String lastFrame = "";
    private final Handler handler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        TOKEN = AgentToken.INSTANCE.ensure(this);

        messages = findViewById(R.id.messages);
        welcome = findViewById(R.id.welcome);
        input = findViewById(R.id.input);
        scroll = findViewById(R.id.scroll);
        capsule = findViewById(R.id.capsule);
        capsuleFrame = findViewById(R.id.capsuleFrame);
        capsuleDot = findViewById(R.id.capsuleDot);
        if (capsule != null) {
            capsuleCollapsed = getSharedPreferences("agent", MODE_PRIVATE)
                    .getBoolean("capsule_collapsed", false);
            capsule.post(() -> {
                applyCapsuleSize(capsuleCollapsed);
                restoreCapsulePosition();
                // Not until there is a picture: an empty black rectangle on the
                // welcome screen reads as a rendering bug. The first frame turns
                // it on (see loadLastFrame).
                showCapsule(false);
            });
            capsule.setOnTouchListener(new CapsuleTouch());
            capsule.getViewTreeObserver().addOnGlobalLayoutListener(this::onRootLayout);
        }
        frameView = findViewById(R.id.frame);
        frameCard = findViewById(R.id.frameCard);
        frameLabel = findViewById(R.id.frameLabel);
        frameCard.setOnClickListener(v -> showFrameViewer());
        statusRow = findViewById(R.id.statusRow);
        statusText = findViewById(R.id.status);
        android.view.View send = findViewById(R.id.send);
        sendButton = send;
        send.setAlpha(1f);
        findViewById(R.id.cancel).setOnClickListener(v -> cancelTask());
        // The capsule is a preference, so it should come back by itself after a
        // restart instead of waiting for someone to open the settings dialog.
        // An older version floated a system overlay; stop any leftover one so
        // there is exactly one mirror, and it belongs to this window.
        stopService(new Intent(this, com.phoneagent.app.CapsuleService.class));
        getSharedPreferences("agent", MODE_PRIVATE).edit()
                .putBoolean("capsule", false).apply();

        input.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == android.view.inputmethod.EditorInfo.IME_ACTION_SEND) {
                send.performClick();
                return true;
            }
            return false;
        });

        handler.postDelayed(pollWatchdog, 4000);
        loadLastFrame();
        handler.postDelayed(frameTicker, 2500);
        ensureSession();
        // the subtitle is the session switcher: it is where the conversation's
        // identity belongs, and it costs no header space
        android.widget.TextView subtitle = findViewById(R.id.subtitle);
        if (subtitle != null) {
            subtitle.setOnClickListener(v -> showSessions());
            refreshSubtitle();
        }
        restoreChat();
        // Diagnostics, not conversation: five lines about Python versions in
        // the middle of the transcript told the user nothing they could act on.
        // They live in 设置 → 诊断 now.
        bootInfo = pythonStatus();
        checkAccessibility();
        checkWirelessDebugging();
        checkBattery();
        checkApiKey();
        resumeRunningTask();
        if (hasIdentity()) {
            startAgentService();
        } else {
            note("还没有配对无线调试，先完成一次配对。");
            startPairingWatch();
        }

        for (int id : new int[]{R.id.chip1, R.id.chip2, R.id.chip3}) {
            TextView chip = findViewById(id);
            chip.setOnClickListener(v -> {
                input.setText(chip.getText());
                input.setSelection(input.getText().length());
            });
        }

        // One button, one panel. A long press used to open a plain list of the
        // same maps; the library shows more (screens, operations, landing rate)
        // and can act on them, so the second door only caused choices.
        findViewById(R.id.maps).setOnClickListener(v -> showMapsLibrary());
        findViewById(R.id.usage).setOnClickListener(v -> showUsage());

        // "CLI+GUI / GUI" was a developer's name for two behaviours, and it left
        // every other reader guessing. The chip now says what it does and opens a
        // choice with one line of plain language each, instead of flipping
        // blindly and explaining itself in the chat.
        modeButton = findViewById(R.id.mode);
        modeButton.setText(mapsMode() ? R.string.mode_auto : R.string.mode_vision);
        modeButton.setOnClickListener(v -> showModeChooser());

        TextView settings = findViewById(R.id.settings);
        settings.setOnClickListener(v -> showSettings());
        settings.setOnLongClickListener(v -> {
            showSystemSettings();
            return true;
        });

        // self-pairing test hook: am start ... --es pair "code@host:port"
        String pairArg = getIntent() == null ? null : getIntent().getStringExtra("pair");
        if (pairArg != null && pairArg.contains(":")) {
            // format: code@host:port
            String hostArg = "127.0.0.1";
            String rest = pairArg;
            if (pairArg.contains("@")) {
                rest = pairArg.substring(pairArg.indexOf('@') + 1);
                hostArg = rest.substring(0, rest.lastIndexOf(':'));
                rest = pairArg.substring(0, pairArg.indexOf('@')) + rest.substring(rest.lastIndexOf(':'));
            }
            final String pairCode = rest.substring(0, rest.lastIndexOf(':'));
            final int pairPort = Integer.parseInt(rest.substring(rest.lastIndexOf(':') + 1));
            final String pairHost = hostArg;
            append("你", "配对 " + pairCode + " @ " + pairHost + ":" + pairPort);
            new Thread(() -> {
                note("正在与手机配对（" + pairHost + "）…");
                com.phoneagent.app.pairing.Pairing.Result r =
                        com.phoneagent.app.pairing.Pairing.INSTANCE.pair(pairHost, pairPort, pairCode, null, "phoneagent", 20000, null);
                android.util.Log.i("Pairing", "result ok=" + r.getOk() + " detail=" + r.getDetail());
                append("PhoneGo", (r.getOk() ? "✅ " : "❌ ") + r.getDetail());
            }).start();
        }

        // handy for testing without typing: am start ... --es task "open bluetooth"
        String preset = getIntent() == null ? null : getIntent().getStringExtra("task");
        if (preset != null && !preset.trim().isEmpty()) {
            append("你", preset);
            startTask(preset.trim());
        }
        send.setOnClickListener(v -> {
            String text = input.getText().toString().trim();
            if (text.isEmpty()) {
                return;
            }
            input.setText("");
            // The keyboard stayed up after sending and covered half of the
            // mirror and the answer: measured on a 1280x2772 screen, the task
            // ran with 40% of the window hidden behind the IME.
            hideKeyboard();
            if (!awaitingTaskId.isEmpty()) {
                // the agent is waiting on a question, so this is the answer
                String id = awaitingTaskId;
                awaitingTaskId = "";
                append("你", text);
                answerTask(id, text);
                return;
            }
            append("你", text);
            startTask(text);
        });
    }

    /** Runs the agent core that ships inside the APK. */
    private String pythonStatus() {
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }
            Python py = Python.getInstance();
            String version = py.getModule("sys").get("version").toString().split("\n")[0];
            PyObject agent = py.getModule("phoneagent");
            PyObject maps = py.getModule("phoneagent.core.app_map");
            PyObject spake2 = py.getModule("spake2");
            PyObject crypto = py.getModule("cryptography");
            String cryptoVersion = py.getModule("cryptography.__about__")
                    .get("__version__").toString();
            return "Python 已就绪：" + version
                    + "\nphoneagent " + agent.get("__version__")
                    + "\napp_map 模块：" + (maps != null ? "已加载" : "缺失")
                    + "\nspake2：" + (spake2 != null ? "可用" : "缺失")
                    + "  cryptography：" + cryptoVersion;
        } catch (Throwable t) {
            return "Python 启动失败：" + t;
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // re-opening the app should retry a start that failed (or was killed),
        // not silently keep a dead agent
        startAgentService();
    }

    private String lastPairHost = "127.0.0.1";

    /** Model settings: the key has to come from the user, not from adb push. */
    /** Package the map builder is currently crawling, if any. */
    private String crawlId = "";
    private String lastCrawlLine = "";

    /**
     * Build a macro map for one app, on the display the agent already owns.
     *
     * The crawl runs inside this process: the phone stays usable, nothing is
     * driven through adb from a computer, and the map is recorded on the very
     * display it will be replayed on -- a map measured anywhere else scores
     * about half as well.
     */
    /**
     * What the agent will actually load, with the two things you can do about
     * it: pin the map to use for a package, or delete a crawl you do not want.
     *
     * One map per package is in use at a time (more screens wins, newest breaks
     * the tie); a pin overrides that until it is removed.
     */
    /**
     * The header line: which conversation you are in, and how to change it.
     *
     * Without the arrow nobody knew the line was tappable -- the session list
     * was found only by accident.
     */
    private void refreshSubtitle() {
        android.widget.TextView subtitle = findViewById(R.id.subtitle);
        if (subtitle == null) {
            return;
        }
        String title = "";
        org.json.JSONArray sessions = readSessions();
        for (int i = 0; i < sessions.length(); i++) {
            org.json.JSONObject entry = sessions.optJSONObject(i);
            if (entry != null && sessionId.equals(entry.optString("id"))) {
                title = entry.optString("title");
                break;
            }
        }
        if (title.isEmpty() || "新会话".equals(title)) {
            title = "会话";
        }
        subtitle.setText(getString(R.string.subtitle_with_arrow, title));
    }

    /**
     * Several conversations with the same phone.
     *
     * Same device, separate transcripts: "what did I ask about Taobao" does not
     * have to scroll past yesterday's experiments.
     */
    /** How old something is, in the terms a person reads it. */
    private static String when(long seconds) {
        if (seconds <= 0) {
            return "时间未知";
        }
        long age = System.currentTimeMillis() / 1000 - seconds;
        if (age < 3600) {
            return (age / 60) + " 分钟前";
        }
        if (age < 86400) {
            return (age / 3600) + " 小时前";
        }
        return (age / 86400) + " 天前";
    }

    private void showSessions() {
        org.json.JSONArray sessions = readSessions();
        String[] labels = new String[sessions.length() + 1];
        labels[0] = "＋ 新会话";
        for (int i = 0; i < sessions.length(); i++) {
            org.json.JSONObject entry = sessions.optJSONObject(i);
            String id = entry == null ? "" : entry.optString("id");
            String title = entry == null ? "" : entry.optString("title");
            labels[i + 1] = (id.equals(sessionId) ? getString(R.string.session_current) + " " : "　 ") + title
                    + " · " + when(entry == null ? 0 : entry.optLong("updated") / 1000);
        }
        new AlertDialog.Builder(this)
                .setTitle("会话")
                .setItems(labels, (d, which) -> {
                    if (which == 0) {
                        saveNow();
                        newSession();
                        conversation.clear();
                        clearChat();
                        stepBlock = null;
                        if (welcome != null) {
                            welcome.setVisibility(View.VISIBLE);
                        }
                        note("新会话 · 之前的内容留在「会话」里");
                        return;
                    }
                    if (!currentTaskId.isEmpty()) {
                        // The task's steps stream into whatever session is open,
                        // so switching mid-task wrote the answer into the wrong
                        // one and left the instruction behind in the old one.
                        android.widget.Toast.makeText(this,
                                "任务还在执行，等它结束再切换会话。",
                                android.widget.Toast.LENGTH_SHORT).show();
                        return;
                    }
                    org.json.JSONObject entry = sessions.optJSONObject(which - 1);
                    if (entry != null) {
                        switchSession(entry.optString("id"));
                    }
                })
                .setNeutralButton("删除当前会话", (d, w) -> confirmDeleteSession())
                .setNegativeButton("关闭", null)
                .show();
    }

    private void confirmDeleteSession() {
        new AlertDialog.Builder(this)
                .setTitle("删除这个会话？")
                .setMessage("只删聊天记录，手机上装的地图和任务结果不受影响。")
                .setPositiveButton("删除", (d, w) -> {
                    java.io.File file = sessionFile();
                    //noinspection ResultOfMethodCallIgnored
                    file.delete();
                    org.json.JSONArray sessions = readSessions();
                    org.json.JSONArray next = new org.json.JSONArray();
                    for (int i = 0; i < sessions.length(); i++) {
                        org.json.JSONObject entry = sessions.optJSONObject(i);
                        if (entry != null && !sessionId.equals(entry.optString("id"))) {
                            next.put(entry);
                        }
                    }
                    writeSessions(next);
                    conversation.clear();
                    clearChat();
                    stepBlock = null;
                    sessionId = "";
                    ensureSession();
                    if (welcome != null) {
                        welcome.setVisibility(View.VISIBLE);
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void startCrawlFor(String pkg, String label, String file) {
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, pad / 2, pad, 0);

        TextView about = new TextView(this);
        about.setText(getString(R.string.crawl_about, label, pkg));
        about.setTextSize(12f);
        about.setTextColor(getColor(R.color.ink_soft));
        box.addView(about);

        box.addView(label("最多爬取屏幕数"));
        EditText screens = new EditText(this);
        screens.setText(R.string.crawl_screens);
        screens.setInputType(InputType.TYPE_CLASS_NUMBER);
        box.addView(screens);

        box.addView(label("时间预算（分钟）"));
        EditText minutes = new EditText(this);
        minutes.setText(R.string.crawl_minutes);
        minutes.setInputType(InputType.TYPE_CLASS_NUMBER);
        box.addView(minutes);

        box.addView(label("最大深度 · 从首页最多点几下"));
        EditText depth = new EditText(this);
        depth.setText("3");
        depth.setInputType(InputType.TYPE_CLASS_NUMBER);
        box.addView(depth);

        new AlertDialog.Builder(this)
                .setTitle("重爬 " + label)
                .setView(box)
                .setPositiveButton("开始", (d, w) -> startCrawl(pkg, label,
                        parseCount(screens.getText().toString(), 12),
                        parseCount(minutes.getText().toString(), 20),
                        parseCount(depth.getText().toString(), 3)))
                .setNegativeButton("取消", null)
                .show();
    }

    private void confirmDelete(JSONObject row) {
        new AlertDialog.Builder(this)
                .setTitle("删除这份地图？")
                .setMessage(row.optString("file") + "\n\n只删手机上的这一份，删了就找不回来。")
                .setPositiveButton("删除", (d, w) -> postMapAction("delete", row, null))
                .setNegativeButton("取消", null)
                .show();
    }

    private void postMapAction(String action, JSONObject row, String file) {
        final String name = file == null ? "" : file;
        new Thread(() -> {
            String problem = "";
            try {
                JSONObject body = new JSONObject();
                body.put("package", row.optString("package"));
                if (!name.isEmpty()) {
                    body.put("file", name);
                } else {
                    body.put("file", row.optString("file"));
                }
                HttpURLConnection conn = connect("/maps/" + action);
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(6000);
                conn.setDoOutput(true);
                conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
                int code = conn.getResponseCode();
                if (code != 200) {
                    problem = "HTTP " + code;
                }
            } catch (Throwable t) {
                problem = t.toString();
            }
            final String failure = problem;
            ui(() -> {
                note(failure.isEmpty() ? "地图已更新" : "地图操作失败：" + failure);
                showMapsLibrary();         // the cards are the one place maps live
            });
        }).start();
    }

    /**
     * The maps this phone has, as cards.
     *
     * This used to be reachable only through 地图 → 构建地图, which is the crawl
     * builder: the maps themselves were invisible, so "which of these actually
     * works" could not be answered from the app at all. The landing rate is the
     * number that decides it, and it is now on the card.
     */
    private void showMapsLibrary() {
        new Thread(() -> {
            try {
                HttpURLConnection conn = connect("/maps");
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(8000);
                JSONObject reply = new JSONObject(read(conn));
                JSONArray maps = reply.optJSONArray("maps");
                ui(() -> renderMapsLibrary(maps == null ? new JSONArray() : maps));
            } catch (Exception e) {
                ui(() -> append("地图", "没能读取地图列表：" + e));
            }
        }).start();
    }

    private void renderMapsLibrary(JSONArray maps) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(12), dp(16), dp(4));

        TextView intro = new TextView(this);
        intro.setText(maps.length() == 0
                ? "还没有地图。建一张之后，同一个 App 的操作会被记下来，下次直接走已知路径。"
                : "落点是这张图的自测成绩：在地图上随机挑几条路径重放，看是否落到记录的页面。");
        intro.setTextSize(12.5f);
        intro.setTextColor(getColor(R.color.ink_soft));
        intro.setPadding(0, 0, 0, dp(10));
        box.addView(intro);

        for (int i = 0; i < maps.length(); i++) {
            JSONObject map = maps.optJSONObject(i);
            if (map == null) {
                continue;
            }
            box.addView(mapCard(map));
        }

        TextView build = new TextView(this);
        build.setText("＋ 构建新地图");
        build.setTextSize(14f);
        build.setTextColor(getColor(R.color.accent));
        build.setGravity(android.view.Gravity.CENTER);
        build.setPadding(0, dp(16), 0, dp(12));
        build.setOnClickListener(v -> showMapBuilder());
        box.addView(build);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(box);

        new AlertDialog.Builder(this)
                .setTitle("地图")
                .setView(scroll)
                .setPositiveButton("关闭", null)
                .show();
    }

    /** One map: what it is, how well it scored, and what can be done to it. */
    private android.view.View mapCard(JSONObject map) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackground(getDrawable(R.drawable.bg_card));
        card.setPadding(dp(14), dp(12), dp(14), dp(6));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(android.view.Gravity.CENTER_VERTICAL);

        TextView title = new TextView(this);
        title.setText(map.optString("app", map.optString("file", "?")));
        title.setTextSize(16f);
        title.setTextColor(getColor(R.color.ink));
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        header.addView(title, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        if (map.optBoolean("pinned", false)) {
            header.addView(pill("正在使用", R.color.accent, R.color.accent_soft));
        }
        card.addView(header);

        TextView pkg = new TextView(this);
        pkg.setText(map.optString("package", ""));
        pkg.setTextSize(11.5f);
        pkg.setTextColor(getColor(R.color.ink_soft));
        card.addView(pkg);

        TextView facts = new TextView(this);
        facts.setTextSize(13f);
        facts.setTextColor(getColor(R.color.ink));
        facts.setPadding(0, dp(8), 0, 0);
        facts.setText(summaryLine(map));
        card.addView(facts);

        TextView score = new TextView(this);
        score.setText(benchLine(map));
        score.setTextSize(12f);
        score.setTextColor(getColor(benchColour(map)));
        score.setPadding(0, dp(2), 0, 0);
        card.addView(score);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(android.view.Gravity.END);

        TextView measure = action("重新测落点", R.color.accent);
        measure.setOnClickListener(v -> startBench(map));
        actions.addView(measure);

        if (map.optBoolean("deletable", false)) {
            TextView remove = action("删除", R.color.err);
            remove.setOnClickListener(v -> new AlertDialog.Builder(this)
                    .setTitle("删除地图")
                    .setMessage("删除「" + map.optString("app") + "」的地图？\n"
                            + "这个 App 会重新变回没有地图的状态。")
                    .setPositiveButton("删除", (d, w) -> postMapChange("/maps/delete",
                            map.optString("file", ""), false))
                    .setNegativeButton("取消", null)
                    .show());
            actions.addView(remove);
        }
        if (map.optBoolean("pinned", false)) {
            TextView unpin = action("取消固定", R.color.ink_soft);
            unpin.setOnClickListener(v -> postMapChange("/maps/unpin",
                    map.optString("package", ""), true));
            actions.addView(unpin);
        } else {
            TextView pin = action("固定使用", R.color.accent);
            pin.setOnClickListener(v -> postMapChange("/maps/pin",
                    map.optString("package", ""), true, map.optString("file", "")));
            actions.addView(pin);
        }
        card.addView(actions);

        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.bottomMargin = dp(10);
        card.setLayoutParams(params);
        return card;
    }

    private String summaryLine(JSONObject map) {
        StringBuilder line = new StringBuilder();
        line.append(map.optInt("screens", 0)).append(" 屏 · ")
                .append(map.optInt("operations", 0)).append(" 条操作");
        JSONObject coverage = map.optJSONObject("coverage");
        if (coverage != null && coverage.optInt("queued", 0) > 0) {
            line.append(" · 还有 ").append(coverage.optInt("queued")).append(" 个没走到");
        }
        String crawled = map.optString("crawled_at", "");
        if (!crawled.isEmpty()) {
            line.append(" · ").append(crawled.replace("T", " "));
        }
        int alternatives = map.optInt("alternatives", 0);
        if (alternatives > 0) {
            // choose() shows one map per package; without this the others are
            // invisible even though they exist and take space.
            line.append(" · 还有 ").append(alternatives).append(" 张同包地图");
        }
        return line.toString();
    }

    /** "落点 4/4" -- the one number that says whether this map is worth using. */
    private String benchLine(JSONObject map) {
        JSONObject bench = map.optJSONObject("bench");
        if (bench == null || bench.length() == 0) {
            return "还没测过落点";
        }
        int ops = bench.optInt("ops", 0);
        if (ops <= 0) {
            // Measured, but the map has no navigation to replay -- every
            // operation is an action on the screen itself. "0/0" would read as
            // a failure of the map.
            return "没有可测的导航操作";
        }
        return "落点 " + bench.optInt("exact", 0) + "/" + ops
                + (bench.optBoolean("stale", false) ? "（旧数据）" : "");
    }

    private int benchColour(JSONObject map) {
        JSONObject bench = map.optJSONObject("bench");
        int ops = bench == null ? 0 : bench.optInt("ops", 0);
        int exact = bench == null ? 0 : bench.optInt("exact", 0);
        if (ops == 0) {
            return R.color.ink_soft;
        }
        if (exact == 0) {
            return R.color.err;
        }
        return exact * 10 >= ops * 8 ? R.color.ok
                : exact * 2 >= ops ? R.color.warn : R.color.err;
    }

    private TextView pill(String text, int ink, int background) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(11f);
        view.setTextColor(getColor(ink));
        android.graphics.drawable.GradientDrawable shape =
                new android.graphics.drawable.GradientDrawable();
        shape.setColor(getColor(background));
        shape.setCornerRadius(dp(9));
        view.setBackground(shape);
        view.setPadding(dp(8), dp(3), dp(8), dp(3));
        return view;
    }

    private TextView action(String text, int colour) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(13f);
        view.setTextColor(getColor(colour));
        view.setPadding(dp(14), dp(4), dp(2), dp(4));
        view.setClickable(true);
        view.setFocusable(true);
        return view;
    }

    private void postMapChange(String path, String value, boolean refresh) {
        postMapChange(path, value, refresh, "");
    }

    /**
     * Replay a few known paths and score the map again.
     *
     * Runs in the background on the agent: each sample relaunches the app and
     * taps its way to a recorded screen, which takes a few seconds apiece.
     */
    private void startBench(JSONObject map) {
        String path = map.optString("dir", "") + "/" + map.optString("file", "");
        new Thread(() -> {
            try {
                HttpURLConnection conn = connect("/bench");
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(8000);
                conn.setRequestProperty("Content-Type", "application/json");
                JSONObject body = new JSONObject();
                body.put("map", path);
                body.put("limit", 6);
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                JSONObject reply = new JSONObject(read(conn));
                String id = reply.optString("id", "");
                ui(() -> append("地图", "正在重新测量「" + map.optString("app") + "」的落点…"));
                pollBench(map, id, 0);
            } catch (Exception e) {
                ui(() -> append("地图", "没能开始测量：" + e));
            }
        }).start();
    }

    private void pollBench(JSONObject map, String id, int attempt) {
        if (id.isEmpty() || attempt > 200) {          // ~10 minutes at 3 s
            return;
        }
        new Thread(() -> {
            try {
                Thread.sleep(3000);
                HttpURLConnection conn = connect("/bench/" + id);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(8000);
                JSONObject state = new JSONObject(read(conn));
                String status = state.optString("state", "");
                if ("done".equals(status) || "failed".equals(status)) {
                    JSONObject summary = state.optJSONObject("summary");
                    String line = "failed".equals(status)
                            ? "测量失败：" + state.optString("error", "")
                            : "「" + map.optString("app") + "」落点 "
                              + (summary == null ? 0 : summary.optInt("exact", 0)) + "/"
                              + (summary == null ? 0 : summary.optInt("ops", 0));
                    ui(() -> append("地图", line));
                    showMapsLibrary();
                    return;
                }
                pollBench(map, id, attempt + 1);
            } catch (Exception e) {
                pollBench(map, id, attempt + 1);
            }
        }).start();
    }

    /** Pin, unpin or delete, then reload the list so the cards tell the truth. */
    private void postMapChange(String path, String value, boolean refresh, String file) {
        new Thread(() -> {
            try {
                HttpURLConnection conn = connect(path);
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(8000);
                conn.setRequestProperty("Content-Type", "application/json");
                JSONObject body = new JSONObject();
                if (path.endsWith("delete")) {
                    body.put("file", value);
                } else {
                    body.put("package", value);
                    if (!file.isEmpty()) {
                        body.put("file", file);
                    }
                }
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                read(conn);
                if (refresh) {
                    ui(() -> { });
                    showMapsLibrary();
                }
            } catch (Exception e) {
                ui(() -> append("地图", "操作失败：" + e));
            }
        }).start();
    }

    private void showMapBuilder() {
        append("构建地图", "正在读取可爬取的 App…");
        new Thread(() -> {
            try {
                HttpURLConnection conn =
                        connect("/crawl/apps");
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(8000);
                JSONObject reply = new JSONObject(read(conn));
                JSONArray apps = reply.optJSONArray("apps");
                if (apps == null || apps.length() == 0) {
                    ui(() -> append("构建地图", "本地 agent 还没有列出 App，稍后再试。"));
                    return;
                }
                List<String> packages = new ArrayList<>();
                List<String> names = new ArrayList<>();
                List<String> labels = new ArrayList<>();
                List<String> states = new ArrayList<>();
                for (int i = 0; i < apps.length(); i++) {
                    JSONObject app = apps.getJSONObject(i);
                    String pkg = app.optString("package");
                    String name = app.optString("label", pkg);
                    String state = app.optString("state", "crawlable");
                    String mark = "mapped".equals(state) ? "📌 "
                            : "refused".equals(state) ? "⛔ "
                            : "risky".equals(state) ? "⚠️ " : "✅ ";
                    packages.add(pkg);
                    names.add(name);
                    states.add(state);
                    labels.add(mark + name + "  (" + pkg + ")");
                }
                ui(() -> showMapDialog(labels, packages, names, states));
            } catch (Exception e) {
                ui(() -> append("构建地图", "没能读取 App 列表：" + e));
            }
        }).start();
    }

    private void showMapDialog(List<String> labels, List<String> packages,
                               List<String> names, List<String> states) {
        float density = getResources().getDisplayMetrics().density;
        int pad = (int) (16 * density);

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, pad, pad, pad);

        TextView hint = new TextView(this);
        hint.setText(R.string.crawl_hint);
        box.addView(hint);

        Spinner spinner = new Spinner(this);
        ArrayAdapter<String> adapter = new ArrayAdapter<>(
                this, android.R.layout.simple_spinner_item, labels);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        // start on the app that was crawled last, or the first one that works
        String last = getSharedPreferences("maps", MODE_PRIVATE).getString("last", "");
        int start = packages.indexOf(last);
        if (start < 0) {
            for (int i = 0; i < states.size(); i++) {
                if (!"refused".equals(states.get(i))) {
                    start = i;
                    break;
                }
            }
        }
        if (start >= 0) spinner.setSelection(start);
        box.addView(spinner);

        // Labels, not hints: the fields are pre-filled, and a hint disappears
        // the moment there is text -- which left three bare numbers.
        box.addView(label("最多爬取屏幕数 · 地图里最多记多少屏"));
        EditText screens = new EditText(this);
        screens.setText(R.string.crawl_screens);
        screens.setInputType(InputType.TYPE_CLASS_NUMBER);
        box.addView(screens);

        box.addView(label("时间预算（分钟）· 到点就收工"));
        EditText minutes = new EditText(this);
        minutes.setText(R.string.crawl_minutes);
        minutes.setInputType(InputType.TYPE_CLASS_NUMBER);
        box.addView(minutes);

        // BFS depth: how many taps away from the home screen the crawl may go.
        // 1 means "open the pages the home screen lists, never tap inside them".
        box.addView(label("最大深度 · 从首页最多点几下（1 = 只进首页列出的页面）"));
        EditText depth = new EditText(this);
        depth.setText("3");
        depth.setInputType(InputType.TYPE_CLASS_NUMBER);
        box.addView(depth);

        new AlertDialog.Builder(this)
                .setTitle("构建地图")
                .setView(box)
                .setPositiveButton("开始", (d, w) -> {
                    int index = spinner.getSelectedItemPosition();
                    if (index < 0 || index >= packages.size()) return;
                    if ("risky".equals(states.get(index))) {
                        new AlertDialog.Builder(this)
                                .setTitle("先试一次")
                                .setMessage(names.get(index) + " 的首页声明为 singleTask，"
                                        + "可能起不来。爬一次就知道：起不来会立刻停下并记住，"
                                        + "下次标成 ⛔。")
                                .setPositiveButton("试", (d2, w2) -> {
                                    int maxScreens = parseCount(screens.getText().toString(), 12);
                                    int maxMinutes = parseCount(minutes.getText().toString(), 20);
                                    int maxDepth = parseCount(depth.getText().toString(), 3);
                                    startCrawl(packages.get(index), names.get(index),
                                            maxScreens, maxMinutes, maxDepth);
                                })
                                .setNegativeButton("算了", null)
                                .show();
                        return;
                    }
                    if ("refused".equals(states.get(index))) {
                        new AlertDialog.Builder(this)
                                .setTitle("这个 App 爬不了")
                                .setMessage(names.get(index) + " 的首页声明为 singleTask，"
                                        + "系统只会把它放回自己所在的屏幕，虚拟屏上起不来。\n\n"
                                        + "换一个标着 ✅ 的 App。")
                                .setPositiveButton("知道了", null)
                                .show();
                        return;
                    }
                    getSharedPreferences("maps", MODE_PRIVATE).edit()
                            .putString("last", packages.get(index)).apply();
                    int maxScreens = parseCount(screens.getText().toString(), 12);
                    int maxMinutes = parseCount(minutes.getText().toString(), 20);
                    startCrawl(packages.get(index), names.get(index), maxScreens, maxMinutes,
                            parseCount(depth.getText().toString(), 3));
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private TextView label(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(12f);
        view.setTextColor(getColor(R.color.ink_soft));
        view.setPadding(0, dp(8), 0, 0);
        return view;
    }

    private static int parseCount(String text, int fallback) {
        try {
            int value = Integer.parseInt(text.trim());
            return value > 0 ? value : fallback;
        } catch (Exception e) {
            return fallback;
        }
    }

    private void startCrawl(String pkg, String name, int maxScreens, int maxMinutes) {
        startCrawl(pkg, name, maxScreens, maxMinutes, 3);
    }

    private void startCrawl(String pkg, String name, int maxScreens, int maxMinutes,
                            int maxDepth) {
        crawlName = name;
        append("构建地图", "开始为「" + name + "」建地图：最多 " + maxScreens
                + " 屏、" + maxMinutes + " 分钟。");
        new Thread(() -> {
            try {
                HttpURLConnection conn = connect("/crawl");
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(15000);
                conn.setRequestProperty("Content-Type", "application/json");
                JSONObject body = new JSONObject();
                body.put("package", pkg);
                body.put("app", name);
                body.put("max_screens", maxScreens);
                body.put("max_minutes", maxMinutes);
                body.put("max_depth", maxDepth);
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                JSONObject reply = new JSONObject(read(conn));
                crawlId = reply.getString("id");
                lastCrawlLine = "";
                ui(() -> appendLines("进度会一行行显示在这里，完成后可以在「地图」里看到它。"
                        + "\n正在爬取（点这里取消）"));
                pollCrawl(0);
            } catch (Exception e) {
                ui(() -> append("构建地图", "没能开始：" + e));
            }
        }).start();
    }

    /** Stop the running crawl; it keeps the screens it already has. */
    private void cancelCrawl() {
        final String id = crawlId;
        if (id.isEmpty()) {
            return;
        }
        note("正在停止爬取…（已爬到的东西会保留）");
        new Thread(() -> {
            try {
                HttpURLConnection conn = connect("/crawl/" + id + "/cancel");
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(8000);
                conn.getOutputStream().write("{}".getBytes("UTF-8"));
                read(conn);
            } catch (Throwable t) {
                note("取消失败：" + t);
            }
        }).start();
    }

    private void pollCrawl(int attempt) {
        if (attempt > 1200 || crawlId.isEmpty()) {   // ~50 minutes at 2.5 s
            return;
        }
        final String id = crawlId;
        new Thread(() -> {
            try {
                Thread.sleep(2500);
                HttpURLConnection conn = connect("/crawl/" + id);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(15000);
                JSONObject state = new JSONObject(read(conn));
                String status = state.optString("state", "?");
                String error = state.optString("error", "");
                JSONArray lines = state.optJSONArray("lines");
                String last = "";
                if (lines != null && lines.length() > 0) {
                    last = lines.getString(lines.length() - 1);
                }
                if ("done".equals(status) || "failed".equals(status)) {
                    final String tail = last;
                    final String took = spentSeconds(tail);
                    int screens = state.optInt("screens", 0);
                    if (screens <= 0) {
                        JSONObject coverage = state.optJSONObject("coverage");
                        if (coverage != null) screens = coverage.optInt("screens", 0);
                    }
                    final int found = screens;
                    final String appName = crawlName.isEmpty() ? "这个 App" : crawlName;
                    ui(() -> append("构建地图", "done".equals(status)
                            ? "「" + appName + "」完成："
                              + (found > 0 ? found + " 屏" : "地图已保存")
                              + (took.isEmpty() ? "" : "，用时 " + took)
                              + "。\n已保存，可以在「地图」里查看或固定使用。"
                            : "「" + appName + "」没能完成：" + error));
                    crawlId = "";
                    return;
                }
                // Only milestones reach the chat. A crawl prints a line every
                // couple of seconds -- "[Enrich] screen_15: 653 chars -> 6
                // items" is a debug trace, not a conversation, and 750 of them
                // made the transcript unreadable (186 of 200 stored entries
                // were this). The full log stays on /crawl/<id>.
                if (!last.isEmpty() && !last.equals(lastCrawlLine) && isMilestone(last)) {
                    lastCrawlLine = last;
                    final String line = friendlyCrawlLine(last);
                    if (!line.isEmpty()) ui(() -> appendLines("  " + line));
                } else if (!last.isEmpty()) {
                    lastCrawlLine = last;
                }
                pollCrawl(attempt + 1);
            } catch (Exception e) {
                // the server may be busy; try again rather than losing the crawl
                pollCrawl(attempt + 1);
            }
        }).start();
    }

    /**
     * The agent's display, full screen and zoomable.
     *
     * The card in the chat is 280dp tall -- enough to see that something is
     * happening, not enough to read a row of text. This view fetches
     * /frame directly so it keeps updating even between task polls, and it
     * keeps the last good frame while a new one is on the way.
     */
    private void showFrameViewer() {
        if (frameDialog != null && frameDialog.isShowing()) {
            return;
        }
        ZoomableImage big = new ZoomableImage(this);
        bigFrame = big;
        big.setBackgroundColor(0xFF111111);
        big.setImageDrawable(frameView.getDrawable());

        TextView hint = new TextView(this);
        hint.setText(R.string.viewer_hint);
        hint.setTextSize(12);
        hint.setTextColor(0xFFBBBBBB);
        hint.setPadding(24, 12, 24, 24);
        hint.setGravity(android.view.Gravity.CENTER);

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackgroundColor(0xFF111111);
        box.addView(big, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        box.addView(hint);

        frameDialog = new android.app.Dialog(this,
                android.R.style.Theme_Material_Light_NoActionBar_Fullscreen);
        frameDialog.setContentView(box);
        frameDialog.setOnDismissListener(d -> frameDialog = null);

        // own refresh: the task poll stops as soon as a task ends, and the
        // point of watching is to see the end happen
        final android.os.Handler handler = new android.os.Handler(getMainLooper());
        final Runnable[] tick = new Runnable[1];
        final boolean[] alive = {true};
        tick[0] = new Runnable() {
            @Override
            public void run() {
                if (!alive[0] || frameDialog == null) {
                    return;
                }
                io.execute(() -> {
                    HttpURLConnection conn = null;
                    try {
                        conn = connect("/frame");
                        conn.setConnectTimeout(2000);
                        conn.setReadTimeout(4000);
                        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                        try (java.io.InputStream in = conn.getInputStream()) {
                            byte[] buffer = new byte[8192];
                            int n;
                            while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
                        }
                        byte[] png = out.toByteArray();
                        Bitmap bitmap = BitmapFactory.decodeByteArray(png, 0, png.length);
                        if (bitmap != null) {
                            ui(() -> {
                                if (frameDialog == null) {
                                    bitmap.recycle();
                                    return;
                                }
                                // One 1280x2772 bitmap per second otherwise: the
                                // viewer is the only place that keeps replacing
                                // the same ImageView while it is open.
                                android.graphics.drawable.Drawable old = big.getDrawable();
                                big.setImageBitmap(bitmap);
                                if (old instanceof android.graphics.drawable.BitmapDrawable) {
                                    android.graphics.Bitmap previous =
                                            ((android.graphics.drawable.BitmapDrawable) old).getBitmap();
                                    if (previous != null && previous != bitmap
                                            && !previous.isRecycled()) {
                                        previous.recycle();
                                    }
                                }
                            });
                        }
                    } catch (Throwable ignored) {
                        // the agent may be between displays; keep the old frame
                    } finally {
                        if (conn != null) conn.disconnect();
                    }
                });
                handler.postDelayed(this, 900);
            }
        };
        frameDialog.setOnDismissListener(d -> {
            alive[0] = false;
            handler.removeCallbacks(tick[0]);
            frameDialog = null;
        });
        handler.post(tick[0]);
        frameDialog.show();
    }

    /**
     * A notification for the moments the agent cannot continue by itself.
     *
     * A task can sit waiting for an answer -- a login, a payment, an app that
     * walked onto the user's screen -- while the user is in another app and
     * cannot see the chat. Without this the task simply appears stuck.
     */
    private void notifyNeedsYou(String question) {
        try {
            android.app.NotificationManager manager =
                    getSystemService(android.app.NotificationManager.class);
            manager.createNotificationChannel(new android.app.NotificationChannel(
                    "attention", "需要你", android.app.NotificationManager.IMPORTANCE_HIGH));
            android.content.Intent open = new android.content.Intent(this, MainActivity.class)
                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            android.app.PendingIntent pending = android.app.PendingIntent.getActivity(
                    this, 9, open,
                    android.app.PendingIntent.FLAG_IMMUTABLE
                            | android.app.PendingIntent.FLAG_UPDATE_CURRENT);
            manager.notify(9, new android.app.Notification.Builder(this, "attention")
                    .setContentTitle("PhoneGo 等你一下")
                    .setContentText(question.length() > 60 ? question.substring(0, 60) + "…" : question)
                    .setStyle(new android.app.Notification.BigTextStyle().bigText(question))
                    .setSmallIcon(android.R.drawable.ic_menu_view)
                    .setContentIntent(pending)
                    .setAutoCancel(true)
                    .build());
        } catch (Throwable t) {
            android.util.Log.w("Pairing", "could not post the attention notification: " + t);
        }
    }

    /**
     * Pick up a task that is still running after the app was closed.
     *
     * The task lives in the agent service, not in this activity: reopening the
     * app used to show nothing at all, which reads as "the task was lost" when
     * the phone was in fact still working on it.
     */
    private void resumeRunningTask() {
        new Thread(() -> {
            try {
                HttpURLConnection conn = connect("/tasks");
                conn.setConnectTimeout(2000);
                conn.setReadTimeout(4000);
                JSONObject data = new JSONObject(read(conn));
                JSONObject tasks = data.optJSONObject("tasks");
                if (tasks == null) {
                    return;
                }
                for (java.util.Iterator<String> it = tasks.keys(); it.hasNext(); ) {
                    String id = it.next();
                    JSONObject task = tasks.optJSONObject(id);
                    if (task == null) {
                        continue;
                    }
                    String state = task.optString("state");
                    if ("running".equals(state) || "queued".equals(state)
                            || "waiting".equals(state)) {
                        final String found = id;
                        ui(() -> {
                            currentTaskId = found;
                            shownSteps = 0;
                            statusRow.setVisibility(View.VISIBLE);
                            statusText.setText("接续上次的任务…");
                            poll(found, 0);
                        });
                        return;
                    }
                }
            } catch (Throwable ignored) {
                // no agent yet; the start path will report that
            }
        }).start();
    }

    /**
     * What the last week of conversations cost.
     *
     * The provider reports token counts with every response; the server keeps
     * them in one file next to the memories and adds them up by day, model and
     * provider.
     */
    /**
     * Token 用量, with the shape of the week rather than a block of text.
     *
     * The numbers were already there but as a paragraph: 389 calls and 1.4 M
     * tokens says nothing about whether today is normal. A bar per day does.
     */
    private void showUsage() {
        new Thread(() -> {
            try {
                HttpURLConnection conn = connect("/usage?days=7");
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(8000);
                JSONObject data = new JSONObject(read(conn));
                ui(() -> renderUsage(data));
            } catch (Throwable t) {
                ui(() -> append("用量", "读不到用量：" + t));
            }
        }).start();
    }

    private void renderUsage(JSONObject data) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(10), dp(16), dp(4));

        JSONObject today = data.optJSONObject("today");
        JSONObject total = data.optJSONObject("total");
        LinearLayout summary = new LinearLayout(this);
        summary.setOrientation(LinearLayout.HORIZONTAL);
        summary.addView(usageFigure("今天", today), new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        summary.addView(usageFigure("近 " + data.optInt("days", 7) + " 天", total),
                new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        box.addView(summary);

        JSONArray byDay = data.optJSONArray("by_day");
        long[] values = new long[7];
        String[] labels = new String[7];
        long max = 1;
        if (byDay != null) {
            for (int i = 0; i < byDay.length() && i < 7; i++) {
                JSONObject day = byDay.optJSONObject(i);
                if (day == null) {
                    continue;
                }
                int slot = 6 - i;                       // newest first -> left to right
                if (slot < 0) {
                    continue;
                }
                values[slot] = day.optLong("total", 0);
                labels[slot] = shortDay(day.optString("day", ""));
                max = Math.max(max, values[slot]);
            }
        }

        TextView chartTitle = new TextView(this);
        chartTitle.setText("每天消耗");
        chartTitle.setTextSize(13f);
        chartTitle.setTextColor(getColor(R.color.ink_soft));
        chartTitle.setPadding(0, dp(14), 0, dp(6));
        box.addView(chartTitle);

        LinearLayout chart = new LinearLayout(this);
        chart.setOrientation(LinearLayout.HORIZONTAL);
        chart.setGravity(android.view.Gravity.BOTTOM);
        for (int i = 0; i < 7; i++) {
            chart.addView(usageBar(labels[i], values[i], max),
                    new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        }
        box.addView(chart);

        JSONArray tasks = data.optJSONArray("by_task");
        java.util.List<View> taskRows = new java.util.ArrayList<>();
        if (tasks != null) {
            for (int i = 0; i < tasks.length(); i++) {
                JSONObject task = tasks.optJSONObject(i);
                if (task == null) {
                    continue;
                }
                String label = task.optString("label", "");
                if (label.isEmpty()) {
                    label = task.optString("task", "?");
                }
                // One line per task: a long instruction wrapped to two lines and
                // five of them filled the page on their own.
                if (label.length() > 24) {
                    label = label.substring(0, 24) + "…";
                }
                TextView row = new TextView(this);
                row.setSingleLine(true);
                row.setEllipsize(android.text.TextUtils.TruncateAt.END);
                row.setText(getString(R.string.usage_task_row, label,
                        task.optInt("calls", 0), tokens(task.optLong("total", 0))));
                row.setTextSize(12.5f);
                row.setTextColor(getColor(R.color.ink));
                row.setPadding(0, dp(3), 0, dp(3));
                taskRows.add(row);
            }
        }
        usageSection(box, "按任务", taskRows, 5);

        JSONArray models = data.optJSONArray("by_model");
        java.util.List<View> modelRows = new java.util.ArrayList<>();
        if (models != null) {
            for (int i = 0; i < models.length(); i++) {
                JSONObject model = models.optJSONObject(i);
                if (model == null) {
                    continue;
                }
                TextView row = new TextView(this);
                row.setText(getString(R.string.usage_model_row, model.optString("model", "?"),
                        model.optInt("calls", 0), tokens(model.optLong("total", 0))));
                row.setTextSize(12.5f);
                row.setTextColor(getColor(R.color.ink));
                row.setPadding(0, dp(3), 0, dp(3));
                modelRows.add(row);
            }
        }
        usageSection(box, "按模型", modelRows, 5);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(box);
        new AlertDialog.Builder(this)
                .setTitle("Token 用量")
                .setView(scroll)
                .setPositiveButton("关闭", null)
                .show();
    }

    /**
     * A usage list that does not grow without end.
     *
     * Both the task and model lists used to print every row they were given, so
     * the page got longer with every task. The first few are always visible and
     * the rest sit behind one toggle.
     */
    private void usageSection(LinearLayout box, String title, java.util.List<View> rows,
                              int preview) {
        if (rows.isEmpty()) {
            return;
        }
        TextView heading = new TextView(this);
        heading.setText(title);
        heading.setTextSize(13f);
        heading.setTextColor(getColor(R.color.ink_soft));
        heading.setPadding(0, dp(16), 0, dp(4));
        box.addView(heading);

        int shown = Math.min(preview, rows.size());
        for (int i = 0; i < shown; i++) {
            box.addView(rows.get(i));
        }
        if (rows.size() <= preview) {
            return;
        }
        TextView more = new TextView(this);
        more.setText(getString(R.string.usage_more, rows.size()));
        more.setTextSize(12.5f);
        more.setTextColor(getColor(R.color.accent));
        more.setPadding(0, dp(6), 0, dp(2));
        box.addView(more);
        more.setOnClickListener(v -> {
            int at = box.indexOfChild(more);
            for (int i = shown; i < rows.size(); i++) {
                box.addView(rows.get(i), at + (i - shown));
            }
            box.removeView(more);
            TextView less = new TextView(this);
            less.setText("收起");
            less.setTextSize(12.5f);
            less.setTextColor(getColor(R.color.accent));
            less.setPadding(0, dp(6), 0, dp(2));
            box.addView(less, at + (rows.size() - shown));
            less.setOnClickListener(v2 -> {
                for (int i = shown; i < rows.size(); i++) {
                    box.removeView(rows.get(i));
                }
                box.removeView(less);
                box.addView(more, at);
            });
        });
    }

    private android.view.View usageFigure(String caption, JSONObject numbers) {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(0, dp(4), dp(6), dp(4));

        TextView label = new TextView(this);
        label.setText(caption);
        label.setTextSize(12f);
        label.setTextColor(getColor(R.color.ink_soft));
        column.addView(label);

        long tokens = numbers == null ? 0 : numbers.optLong("total", 0);
        int calls = numbers == null ? 0 : numbers.optInt("calls", 0);

        TextView big = new TextView(this);
        big.setText(tokens(tokens));
        big.setTextSize(22f);
        big.setTextColor(getColor(R.color.ink));
        big.setTypeface(null, android.graphics.Typeface.BOLD);
        column.addView(big);

        TextView small = new TextView(this);
        small.setText(getString(R.string.usage_calls, calls));
        small.setTextSize(12f);
        small.setTextColor(getColor(R.color.ink_soft));
        column.addView(small);
        return column;
    }

    /** One bar: value on top, height proportional, day underneath. */
    private android.view.View usageBar(String label, long value, long max) {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setGravity(android.view.Gravity.CENTER_HORIZONTAL);
        column.setPadding(dp(3), 0, dp(3), 0);

        TextView amount = new TextView(this);
        amount.setText(value <= 0 ? "" : compact(value));
        amount.setTextSize(9.5f);
        amount.setTextColor(getColor(R.color.ink_soft));
        amount.setGravity(android.view.Gravity.CENTER);
        column.addView(amount);

        View bar = new View(this);
        int height = value <= 0 ? dp(3) : (int) (dp(96) * value / Math.max(1, max));
        android.graphics.drawable.GradientDrawable shape =
                new android.graphics.drawable.GradientDrawable();
        shape.setColor(getColor(value <= 0 ? R.color.line : R.color.accent));
        shape.setCornerRadius(dp(5));
        bar.setBackground(shape);
        LinearLayout.LayoutParams barParams =
                new LinearLayout.LayoutParams(Math.min(dp(28), dp(200)), Math.max(dp(3), height));
        // the bars sit on a common baseline
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.VERTICAL);
        wrapper.setGravity(android.view.Gravity.BOTTOM);
        wrapper.setMinimumHeight(dp(100));
        wrapper.addView(bar, barParams);
        column.addView(wrapper);

        TextView day = new TextView(this);
        day.setText(label);
        day.setTextSize(9.5f);
        day.setTextColor(getColor(R.color.ink_soft));
        day.setGravity(android.view.Gravity.CENTER);
        column.addView(day);
        return column;
    }

    /** "23:41" -- the clock time of a message. */
    private static String hhmm(long millis) {
        return new java.text.SimpleDateFormat("HH:mm", java.util.Locale.US)
                .format(new java.util.Date(millis));
    }

    private static String shortDay(String iso) {
        if (iso == null || iso.length() < 10) {
            return "";
        }
        return iso.substring(5);                 // "11-12"
    }

    private static String tokens(long value) {
        if (value >= 1_000_000) {
            return String.format(java.util.Locale.US, "%.1fM", value / 1_000_000.0);
        }
        if (value >= 1_000) {
            return String.format(java.util.Locale.US, "%.0fK", value / 1_000.0);
        }
        return String.valueOf(value);
    }

    private static String compact(long value) {
        if (value >= 1_000_000) {
            return String.format(java.util.Locale.US, "%.1fM", value / 1_000_000.0);
        }
        if (value >= 1_000) {
            return Math.round(value / 1_000.0) + "K";
        }
        return String.valueOf(value);
    }

    /** Bring the chat back to the front after a task driven on the user's screen. */
    private void returnToChat() {
        try {
            android.content.Intent back = new android.content.Intent(this, MainActivity.class);
            back.addFlags(android.content.Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                    | android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(back);
        } catch (Throwable ignored) {
            // if it does not come forward the answer is still in the chat
        }
    }

    private void showSettings() {
        AgentConfig.Config cfg = AgentConfig.load(this);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, pad / 2, pad, 0);

        EditText key = new EditText(this);
        key.setHint("API Key（sk-or-v1-…）");
        key.setText(cfg.getApiKey());
        key.setSingleLine(true);

        EditText model = new EditText(this);
        model.setHint("model");
        model.setText(cfg.getModel());
        model.setSingleLine(true);

        EditText base = new EditText(this);
        base.setHint("api base（例如 https://api.deepseek.com/v1）");
        base.setText(cfg.getApiBase());
        base.setSingleLine(true);

        EditText vision = new EditText(this);
        vision.setHint("视觉模型（留空则与上面相同）");
        vision.setText(cfg.getVisionModel());
        vision.setSingleLine(true);

        EditText rounds = new EditText(this);
        rounds.setHint("每条任务最多走几步（默认 35）");
        rounds.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        rounds.setText(cfg.getMaxRounds() > 0 ? String.valueOf(cfg.getMaxRounds()) : "35");
        rounds.setSingleLine(true);

        // Switching provider used to mean knowing the base URL and a model id
        // by heart. These fill both in; the fields stay editable.
        LinearLayout presetRow = new LinearLayout(this);
        presetRow.setOrientation(LinearLayout.HORIZONTAL);
        // A plain horizontal LinearLayout squeezes the last chip into a
        // one-character-wide column; scrolling keeps every label readable.
        android.widget.HorizontalScrollView presetScroll =
                new android.widget.HorizontalScrollView(this);
        presetScroll.setHorizontalScrollBarEnabled(false);
        presetScroll.addView(presetRow);
        presetScroll.setPadding(0, dp(6), 0, dp(2));
        String[][] choices = {
                {"OpenRouter", "https://openrouter.ai/api/v1", "qwen/qwen3.8-flash"},
                // DeepSeek's own Anthropic-protocol endpoint: the base URL is
                // what tells the agent which protocol to speak.
                {"DeepSeek 官方", "https://api.deepseek.com/anthropic", "deepseek-flash"},
                {"DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"},
                {"智谱", "https://open.bigmodel.cn/api/paas/v4", "glm-4v-flash"},
                {"阿里百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-vl-plus"},
                {"硅基流动", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-VL-72B-Instruct"},
                {"本地", "http://localhost:8000/v1", "Qwen/Qwen2.5-VL-7B-Instruct"},
        };
        for (String[] choice : choices) {
            TextView chip = new TextView(this);
            chip.setText(choice[0]);
            chip.setTextSize(12f);
            chip.setPadding(dp(8), dp(6), dp(8), dp(6));
            android.widget.LinearLayout.LayoutParams lp =
                    new android.widget.LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.WRAP_CONTENT,
                            ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.rightMargin = dp(6);
            chip.setLayoutParams(lp);
            chip.setOnClickListener(v -> {
                base.setText(choice[1]);
                model.setText(choice[2]);
            });
            presetRow.addView(chip);
        }

        TextView test = new TextView(this);
        test.setText("测试连接");
        test.setTextSize(13f);
        test.setTextColor(getColor(R.color.accent));
        test.setPadding(0, dp(10), 0, 0);
        test.setOnClickListener(v -> {
            test.setText("测试中…");
            new Thread(() -> {
                String result;
                try {
                    org.json.JSONObject body = new org.json.JSONObject()
                            .put("api_key", key.getText().toString().trim())
                            .put("api_base", base.getText().toString().trim())
                            .put("model", model.getText().toString().trim())
                            .put("vision_model", vision.getText().toString().trim());
                    HttpURLConnection conn = connect("/test-llm");
                    conn.setRequestMethod("POST");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(30000);
                    conn.setRequestProperty("Content-Type", "application/json");
                    conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
                    org.json.JSONObject out = new org.json.JSONObject(read(conn));
                    result = out.optBoolean("ok")
                            ? "✅ 可用：" + out.optString("detail")
                            : "❌ " + out.optString("detail");
                } catch (Throwable t) {
                    result = "❌ " + t;
                }
                final String text = result;
                ui(() -> test.setText(text));
            }).start();
        });

        // The system-overlay capsule is gone: the mirror is a view inside this
        // window now (see the layout), so nothing needs a permission and nothing
        // floats over other apps. The old service only drew that overlay.

        CheckBox autonomous = new CheckBox(this);
        autonomous.setText("自主模式：花钱前也不问");
        autonomous.setChecked(autonomousMode());

        TextView diag = new TextView(this);
        diag.setText("诊断：查看运行环境");
        diag.setTextSize(13f);
        diag.setTextColor(getColor(R.color.ink_soft));
        diag.setPadding(0, dp(14), 0, dp(4));
        diag.setOnClickListener(v -> new AlertDialog.Builder(this)
                .setTitle("运行环境")
                .setMessage((bootInfo.isEmpty() ? "尚未启动" : bootInfo)
                        + "\n\n心跳：" + beatLine())
                .setPositiveButton("知道了", null)
                .setNeutralButton("立即重连", (d2, w2) -> {
                    alert("正在检查连接…");
                    healAgent(true);
                })
                .show());

        box.addView(key);
        box.addView(model);
        box.addView(base);
        box.addView(presetScroll);
        box.addView(vision);
        box.addView(rounds);
        box.addView(test);
        box.addView(autonomous);
        box.addView(diag);

        new AlertDialog.Builder(this)
                .setTitle("模型设置")
                .setView(box)
                .setPositiveButton("保存", (d, w) -> {
                    getSharedPreferences("agent", MODE_PRIVATE).edit()
                            .putBoolean("autonomous", autonomous.isChecked()).apply();
                    int steps;
                    try {
                        steps = Integer.parseInt(rounds.getText().toString().trim());
                    } catch (NumberFormatException e) {
                        steps = 0;
                    }
                    // Clamped rather than rejected: a typo should not silently
                    // become a one-step task.
                    steps = Math.max(5, Math.min(80, steps));
                    saveAgentConfig(
                            key.getText().toString().trim(),
                            base.getText().toString().trim(),
                            model.getText().toString().trim(),
                            vision.getText().toString().trim(),
                            steps);
                })
                .setNeutralButton("配对无线调试", (d, w) -> startPairingWatch())
                .setNegativeButton("取消", null)
                .show();
    }

    private static final int REQUEST_BACKUP_IMPORT = 1001;
    private TextView modeButton;

    /**
     * Two ways to drive a phone, described without the jargon.
     *
     * A list dialog clipped the second hint: AlertController gives its list a
     * fixed maximum height and the item text is taller than one line. Two rows
     * in a plain LinearLayout size themselves.
     */
    private void showModeChooser() {
        boolean auto = mapsMode();
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(8), dp(20), dp(4));

        android.widget.RadioButton autoRow = modeOption(
                getString(R.string.mode_auto), getString(R.string.mode_auto_hint), auto);
        android.widget.RadioButton visionRow = modeOption(
                getString(R.string.mode_vision), getString(R.string.mode_vision_hint), !auto);
        box.addView(autoRow);
        box.addView(visionRow);

        // 托管模式 belongs here, not buried in 设置: it changes what the user will
        // see happen on their own screen, which is the most surprising thing this
        // app can do.
        android.widget.CheckBox handsOff = new android.widget.CheckBox(this);
        handsOff.setText(handsOffLabel());
        handsOff.setTextSize(14f);
        handsOff.setChecked(handsOffMode());
        handsOff.setPadding(0, dp(12), 0, dp(2));
        handsOff.setOnCheckedChangeListener((b, checked) -> {
            getSharedPreferences("agent", MODE_PRIVATE).edit()
                    .putBoolean("hands_off", checked).apply();
            android.widget.Toast.makeText(this,
                    checked ? R.string.hands_off_on_toast : R.string.hands_off_off_toast,
                    android.widget.Toast.LENGTH_SHORT).show();
        });
        box.addView(handsOff);

        TextView handsHint = new TextView(this);
        handsHint.setText(R.string.hands_off_hint);
        handsHint.setTextSize(11.5f);
        handsHint.setTextColor(getColor(R.color.ink_soft));
        handsHint.setPadding(dp(36), 0, 0, dp(6));
        box.addView(handsHint);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle(R.string.mode_title)
                .setView(box)
                .setNegativeButton("取消", null)
                .create();

        autoRow.setOnClickListener(v -> {
            applyMode(true);
            dialog.dismiss();
        });
        visionRow.setOnClickListener(v -> {
            applyMode(false);
            dialog.dismiss();
        });
        dialog.show();
    }

    private String beatLine() {
        long seconds = AgentHealth.beatSeconds(this);
        if (seconds < 0) {
            return "还没有心跳记录";
        }
        return seconds <= 90 ? seconds + " 秒前（正常）" : seconds + " 秒前（可能卡住）";
    }

    private String handsOffLabel() {
        return getString(R.string.hands_off_label);
    }

    /** One choice: the name, and a line saying what it does. */
    private android.widget.RadioButton modeOption(String title, String hint, boolean checked) {
        android.widget.RadioButton row = new android.widget.RadioButton(this);
        row.setText(getString(R.string.mode_option, title, hint));
        row.setTextSize(14f);
        row.setChecked(checked);
        row.setButtonTintList(null);
        row.setPadding(0, dp(10), 0, dp(10));
        row.setOnClickListener(v -> { });
        return row;
    }

    private void applyMode(boolean auto) {
        setMapsMode(auto);
        if (modeButton != null) {
            modeButton.setText(auto ? R.string.mode_auto : R.string.mode_vision);
        }
        android.widget.Toast.makeText(this,
                auto ? R.string.mode_auto_toast : R.string.mode_vision_toast,
                android.widget.Toast.LENGTH_SHORT).show();
    }

    private boolean isCapsuleOn() {
        return getSharedPreferences("agent", MODE_PRIVATE).getBoolean("capsule", false);
    }

    private void startCapsule() {
        getSharedPreferences("agent", MODE_PRIVATE).edit().putBoolean("capsule", true).apply();
        // Without POST_NOTIFICATIONS the capsule has no way to reach the user
        // when a background activity start is refused (measured: granted=false
        // is the default on Android 13+, and the tapping notification never
        // appeared).
        // minSdk is 34, so POST_NOTIFICATIONS always exists; no version guard.
        if (checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 7);
        }
        startForegroundService(new Intent(this, CapsuleService.class));
    }

    private void stopCapsule() {
        getSharedPreferences("agent", MODE_PRIVATE).edit().putBoolean("capsule", false).apply();
        stopService(new Intent(this, CapsuleService.class));
    }

    /** Ask mode by default: a step that spends money waits for the user. */
    /**
     * CLI+GUI by default: replay the compiled map first, fall back to vision.
     * GUI-only drops the maps entirely and drives the phone from screenshots.
     */
    private boolean mapsMode() {
        return getSharedPreferences("agent", MODE_PRIVATE).getBoolean("maps", true);
    }

    private void setMapsMode(boolean on) {
        getSharedPreferences("agent", MODE_PRIVATE).edit().putBoolean("maps", on).apply();
    }

    /** Drive the phone's own screen instead of a virtual display. */
    private boolean handsOffMode() {
        return getSharedPreferences("agent", MODE_PRIVATE)
                .getBoolean("hands_off", false);
    }

    private boolean autonomousMode() {
        return getSharedPreferences("agent", MODE_PRIVATE).getBoolean("autonomous", false);
    }

    private void saveAgentConfig(String apiKey, String apiBase, String model,
                                 String visionModel, int maxRounds) {
        try {
            AgentConfig.save(this, apiKey, apiBase, model, visionModel, maxRounds, "");
            note(apiKey.isEmpty()
                    ? "已清空密钥，agent 只能截图模式运行。"
                    : "已保存，正在重启本地 agent…");
            AgentService.restart(this);
        } catch (Exception e) {
            append("PhoneGo", "保存失败：" + e);
        }
    }

    /**
     * Without a key the task server declines to start, and that failure is
     * invisible from here -- the user just sees nothing happen.
     */
    private void checkApiKey() {
        if (!AgentConfig.hasKey(this)) {
            note("还没有配置模型密钥。点「设置」填入 API Key，否则任务无法执行。");
        }
    }

    private boolean accessibilityWarned;

    /**
     * Worth saying out loud: without the service the agent can only look at
     * pixels. But do not say it too early -- the Python side turns the service
     * back on itself as soon as it has its adb shell (MIUI forgets it after a
     * reboot), so a check at launch reports a state that is about to change.
     * Wait for the repair, and if it worked, say that instead.
     */
    private void checkAccessibility() {
        // Adb comes up around 20 s in on this phone; warning before the repair
        // has had a chance is how the app ended up telling people the service
        // was off while it was already on.
        checkAccessibility(22000);
    }

    private int accessibilityChecks;

    private void checkAccessibility(long delayMs) {
        new android.os.Handler(getMainLooper()).postDelayed(() -> {
            boolean enabled = accessibilityEnabled();
            if (enabled) {
                if (accessibilityWarned) {
                    accessibilityWarned = false;
                    note("✅ 无障碍服务已就绪（启动时由 App 自动恢复）。");
                }
                return;
            }
            if (!accessibilityWarned) {
                accessibilityWarned = true;
                note("提示：无障碍服务未开启。没有它，PhoneGo 无法读取配对码，"
                        + "agent 也只能用截图（无法按文字标签点击）。"
                        + "\n长按「设置」可以直接打开无障碍设置页。");
            }
            // The repair happens once adb is up, which can be 20 s in; keep
            // looking for a while so the warning clears itself instead of
            // staying on screen after the problem is gone.
            if (accessibilityChecks++ < 6) {
                checkAccessibility(12000);
            }
        }, delayMs);
    }

    private static String readText(java.io.File file) throws Exception {
        StringBuilder sb = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new java.io.InputStreamReader(new java.io.FileInputStream(file), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                sb.append(line);
            }
        }
        return sb.toString();
    }

    /**
     * Brings up the in-app agent: virtual display plus the HTTP server the
     * chat below talks to. Everything happens on this phone -- the server is
     * in this process, not on a host reached through `adb reverse`.
     */
    private void startAgentService() {
        if (!hasIdentity()) {
            note("还没有可用的配对身份，请先配对无线调试。");
            return;
        }
        if (!wirelessDebuggingEnabled()) {
            note("无线调试当前是关闭的，PhoneGo 连不上自己的 adbd。"
                    + "\n请到「设置 → 开发者选项 → 无线调试」打开开关，再回到这里。");
            return;
        }
        try {
            startForegroundService(new Intent(this, AgentService.class));
            note("正在启动本地 agent（查找 adbd、建立虚拟屏）…");
        } catch (Throwable t) {
            append("PhoneGo", "启动本地 agent 失败：\n" + t);
        }
    }

    private boolean hasIdentity() {
        java.io.File dir = new java.io.File(getFilesDir(), "identity");
        return new java.io.File(dir, "pair-cert.pem").exists()
                && new java.io.File(dir, "pair-key.pem").exists();
    }

    /**
     * HyperOS kills a process that only holds a foreground service, so the
     * agent has to be exempt from battery optimisation to survive in the
     * background. There is no API to grant it; the best we can do is take the
     * user to the right page.
     */
    private void showSystemSettings() {
        new AlertDialog.Builder(this)
                .setTitle("系统设置")
                .setItems(new String[]{
                        "无障碍服务", "电池优化（后台运行）",
                        "导出备份（含 API Key）", "导入备份"}, (d, which) -> {
                    if (which == 0) {
                        startActivity(new Intent(
                                android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS));
                    } else if (which == 1) {
                        requestBatteryExemption();
                    } else if (which == 2) {
                        exportBackup();
                    } else {
                        pickBackup();
                    }
                })
                .show();
    }

    /**
     * A reinstall used to mean re-pairing, re-entering the key and losing every
     * crawled map: they live in the app's private data, and the key is sealed
     * with a Keystore key that dies with the install. One zip brings all of it
     * back; the dialog says the key is in the clear before the file is written.
     */
    private void exportBackup() {
        new AlertDialog.Builder(this)
                .setTitle("导出备份")
                .setMessage("会把配对身份、爬好的地图、会话记录和模型设置打成一个 zip，"
                        + "存到「下载」目录。\n\n"
                        + "文件里的 API Key 是明文（它在本机是加密的，换机后无法解密），"
                        + "请不要直接分享给别人。")
                .setPositiveButton("导出", (d, w) -> {
                    note("正在导出备份…");
                    new Thread(() -> {
                        try {
                            String name = Backup.exportToDownloads(this);
                            runOnUiThread(() -> note("备份已保存到「下载」：" + name));
                        } catch (Exception e) {
                            runOnUiThread(() -> note("导出失败：" + e.getMessage()));
                        }
                    }, "backup-export").start();
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void pickBackup() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"application/zip",
                "application/octet-stream"});
        try {
            startActivityForResult(intent, REQUEST_BACKUP_IMPORT);
        } catch (Exception e) {
            note("打不开文件选择器：" + e.getMessage());
        }
    }

    private void importBackup(Uri uri) {
        note("正在导入备份…");
        new Thread(() -> {
            try {
                int files = Backup.restore(this, uri);
                runOnUiThread(() -> {
                    note("已恢复 " + files + " 个文件。正在重启 agent…");
                    healAgent(true);
                });
            } catch (Exception e) {
                runOnUiThread(() -> note("导入失败：" + e.getMessage()));
            }
        }, "backup-import").start();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_BACKUP_IMPORT && resultCode == RESULT_OK
                && data != null && data.getData() != null) {
            importBackup(data.getData());
        }
    }

    /**
     * Play policy dislikes this dialog; MIUI needs it. Without an exemption the
     * system freezes the agent's process mid-task (measured on this phone), so
     * the trade is deliberate.
     */
    @android.annotation.SuppressLint("BatteryLife")
    private void requestBatteryExemption() {
        PowerManager power = getSystemService(PowerManager.class);
        if (power != null && power.isIgnoringBatteryOptimizations(getPackageName())) {
            note("已经不受电池优化限制，后台可以常驻。");
            return;
        }
        try {
            startActivity(new Intent(
                    android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + getPackageName())));
        } catch (Exception e) {
            // MIUI replaces the stock page with its own
            try {
                startActivity(new Intent().setComponent(new ComponentName(
                        "com.miui.securitycenter",
                        "com.miui.permcenter.autostart.AutoStartManagementActivity")));
            } catch (Exception inner) {
                append("PhoneGo", "打不开电池设置，请手动到「设置 → 应用 → PhoneGo → 省电策略 → 无限制」。");
            }
        }
    }

    /** Without this adbd has no TLS listener at all, so nothing can connect. */
    private boolean wirelessDebuggingEnabled() {
        return android.provider.Settings.Global.getInt(
                getContentResolver(), "adb_wifi_enabled", 0) == 1;
    }

    private void checkWirelessDebugging() {
        if (!wirelessDebuggingEnabled()) {
            note("提示：无线调试是关闭的，adbd 没有监听端口。"
                    + "\n请到「设置 → 开发者选项 → 无线调试」打开开关。");
        }
    }

    private void checkBattery() {
        PowerManager power = getSystemService(PowerManager.class);
        if (power != null && !power.isIgnoringBatteryOptimizations(getPackageName())) {
            note("提示：电池优化可能会在后台杀掉 PhoneGo。"
                    + "\n长按「设置」→「电池优化」可以把它设为不限制。");
        }
    }

    private boolean accessibilityEnabled() {
        String enabled = android.provider.Settings.Secure.getString(
                getContentResolver(),
                android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        return enabled != null && enabled.contains(getPackageName());
    }

    /**
     * Pairs without the user retyping anything: the service watches the
     * screen, and the system's pairing dialog is the only place the code
     * exists. It also dies as soon as that dialog is dismissed, so this has to
     * run in the background while the user stays on the Settings screen --
     * which is exactly why typing the code in by hand cannot work either.
     */
    private void startPairingWatch() {
        if (!accessibilityEnabled()) {
            new AlertDialog.Builder(this)
                    .setTitle("需要先开启无障碍服务")
                    .setMessage("配对码只存在于系统的配对弹窗里，而且弹窗一关配对服务就停了，"
                            + "所以只能由 PhoneGo 自己读取。\n\n"
                            + "请到「设置 → 无障碍」开启 PhoneGo，再回来配对。")
                    .setPositiveButton("打开无障碍设置", (d, w) ->
                            startActivity(new Intent(
                                    android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS)))
                    .setNegativeButton("取消", null)
                    .show();
            return;
        }
        try {
            Intent intent = new Intent(this, PairingService.class);
            intent.putExtra("watch", true);
            startForegroundService(intent);
            note("请打开 设置 → 开发者选项 → 无线调试 → 使用配对码配对设备。"
                    + "\n配对码由 PhoneGo 自己读取，不用手输；弹出后两分钟内完成。");
        } catch (Throwable t) {
            append("PhoneGo", "启动配对失败：\n" + t);
        }
    }

    private void startTask(String text) {
        new Thread(() -> {
            Exception last = null;
            // the display takes a few seconds to come up; retry rather than
            // failing the user's first message
            for (int attempt = 0; attempt < 60; attempt++) {
                try {
                    HttpURLConnection conn = connect("/task");
                    conn.setRequestMethod("POST");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(3000);
                    conn.setReadTimeout(15000);
                    conn.setRequestProperty("Content-Type", "application/json");
                    JSONObject body = new JSONObject();
                    body.put("text", text);
                    body.put("autonomous", autonomousMode());
                    body.put("maps", mapsMode());
                    if (handsOffMode()) {
                        // the user handed the phone over: drive their own screen
                        body.put("display", 0);
                    }
                    try (OutputStream out = conn.getOutputStream()) {
                        out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                    }
                    final String id = new JSONObject(read(conn)).getString("id");
                    ui(() -> {
                        currentTaskId = id;
                        shownSteps = 0;
                        lastFrame = "";
                        setBusy(true);
                        // The status row already says 执行中… and offers 取消;
                        // a task id in the transcript was pure noise.
                        showThinking();
                    });
                    poll(id, 0);
                    return;
                } catch (Exception e) {
                    last = e;
                    if (attempt == 0) {
                        ui(() -> append("PhoneGo", "等待本地 agent 就绪…"));
                    }
                    try {
                        Thread.sleep(1000);
                    } catch (InterruptedException ignored) {
                        return;
                    }
                }
            }
            final Exception e = last;
            boolean noKey = !AgentConfig.hasKey(this);
            ui(() -> append("PhoneGo", noKey
                    ? "任务没能提交：还没有配置模型密钥，点「设置」填入 API Key。\n" + e
                    : "本地 agent 没有起来：\n" + e));
        }).start();
    }

    @Override
    protected void onDestroy() {
        // A Dialog holds a window on this activity; leaving one showing while
        // the activity goes away is a leaked window (logcat: "WindowLeaked").
        if (frameDialog != null) {
            frameDialog.dismiss();
            frameDialog = null;
        }
        // Every repeating runnable on this handler (the mirror ticker, the
        // thinking dots, the capsule fold timer) captures `this`. Left running
        // after the activity is gone they keep it, and its whole view tree,
        // reachable from a static handler queue.
        handler.removeCallbacksAndMessages(null);
        polling = false;
        super.onDestroy();
    }

    @Override
    protected void onPause() {
        super.onPause();
        saveNow();          // a session switch or a kill must not lose the last line
    }

    @Override
    protected void onResume() {
        super.onResume();
        // The app can come back from a freeze with its poll chain gone; ask
        // about the task again instead of showing a stale "执行中…". The age of
        // the last answer is the only trustworthy signal -- `polling` stays
        // true when the chain died silently.
        if (!currentTaskId.isEmpty()
                && System.currentTimeMillis() - lastPollAt > 15000) {
            poll(currentTaskId, 0);
        }
        // Coming back to the app is the moment the user expects it to work
        // again: wireless debugging may have been off while it was away, and
        // waiting for the next task to find that out wastes the first task.
        handler.postDelayed(this::healAgent, 1200);
    }

    /**
     * Rebuild the display if the adb connection died while the app was away.
     *
     * Runs off the UI thread (the check is a shell command on the phone's own
     * adbd) and at most once every half minute, so switching apps does not turn
     * into a reconnect loop.
     */
    private void healAgent() {
        healAgent(false);
    }

    /** manual: the user pressed 立即重连, so skip the throttle and say what happened. */
    /** Last warm-up of the mapped apps, so a resume does not repeat it. */
    private long lastWarmup = 0;

    private void healAgent(boolean manual) {
        long now = System.currentTimeMillis();
        // Only once the agent is up: "started" is set before the display exists,
        // and a check in that window would rebuild the display the service is
        // still building, leaving the server down.
        if (!currentTaskId.isEmpty() || !crawlId.isEmpty()) {
            // A task or a crawl owns the adb transport: it is one channel and
            // everything
            // travels in order. Asking healthy() from here interleaved a shell
            // command with the agent's own reads and corrupted the stream --
            // "frame magic mismatch" on three tasks in a row, and then again on
            // every crawl, because a crawl only skips the heal when a *task*
            // is running.
            return;
        }
        boolean ready = AgentService.isReady();
        // Off unless asked for. The warm-up launches the first mapped apps
        // (Settings, 大众点评, 小红书) on the agent's display, and to anyone
        // watching the phone it looks exactly like the agent wandering into the
        // wrong app in the middle of a task -- measured: a 小红书→携程 task that
        // opened 大众点评 while it was being set up. The saving (nine steps
        // instead of four on the first task) is not worth that.
        boolean warmup = getSharedPreferences("agent", MODE_PRIVATE)
                .getBoolean("warmup", false);
        if (ready && warmup && System.currentTimeMillis() - lastWarmup > 10 * 60 * 1000L) {
            lastWarmup = System.currentTimeMillis();
            new Thread(() -> {
                try {
                    HttpURLConnection conn = connect("/warmup");
                    conn.setRequestMethod("POST");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(3000);
                    conn.setReadTimeout(8000);
                    conn.getOutputStream().write("{}".getBytes("UTF-8"));
                    read(conn);
                } catch (Throwable ignored) {
                    // a warm-up that fails costs nothing
                }
            }).start();
        }
        long beat = AgentHealth.beatSeconds(this);
        android.util.Log.i("Pairing", "heal: ready=" + ready + " beat=" + beat
                + "s since=" + (now - lastHealAt) / 1000 + "s");
        if (!ready) {
            // Coming back to a freshly started app: the display takes twenty
            // seconds, so a single check at 1.2 s always finds it unready and
            // the heal would never run when it is most needed.
            if (manual) {
                alert("agent 还在启动，请稍等十几秒再试。");
                return;
            }
            if (healAttempts < 10) {
                healAttempts++;
                handler.postDelayed(this::healAgent, 3000);
            }
            return;
        }
        healAttempts = 0;
        if (!manual && now - lastHealAt < 30000) {
            return;
        }
        lastHealAt = now;
        // A frozen interpreter cannot answer healthy(); asking it would hang this
        // check forever. The heartbeat file is the one signal that survives.
        if (beat > 120) {
            alert("PhoneGo 的后台进程卡住了（" + beat + " 秒没有心跳）。"
                    + "\n请从最近任务里划掉它，再重新打开一次。");
            return;
        }
        new Thread(() -> {
            String verdict = "";
            try {
                PyObject ondevice = Python.getInstance().getModule("phoneagent.ondevice");
                boolean healthy = Boolean.parseBoolean(
                        ondevice.callAttr("healthy").toString());
                android.util.Log.i("Pairing", "heal: healthy=" + healthy);
                if (healthy) {
                    if (manual) {
                        ui(() -> alert("连接正常，不需要重连。"));
                    }
                    return;
                }
                // recover_json answers {"ok": ..., "message": ...}. The plain
                // recover() returns a sentence whose success form is
                // "display=367 size=1280x2772 dpi=520 app=..." -- that string
                // reached the chat once and looked like a bug report.
                org.json.JSONObject recovery =
                        new org.json.JSONObject(ondevice.callAttr("recover_json").toString());
                boolean rebuilt = recovery.optBoolean("ok", false);
                android.util.Log.i("Pairing", "heal: rebuilt=" + rebuilt
                        + " via " + recovery.optString("message", ""));
                if (rebuilt) {
                    verdict = manual ? "已重连，可以继续使用。" : "已自动重连，可以继续使用。";
                } else {
                    verdict = recovery.optString("message", "");
                }
            } catch (Throwable t) {
                verdict = "重连失败：" + t;
                android.util.Log.w("Pairing", "heal failed", t);
            }
            final String result = verdict;
            ui(() -> {
                if (result.isEmpty() || result.startsWith("nothing")) {
                    return;
                }
                alert(result);
            });
        }).start();
    }

    private void poll(String id, int attempt) {
        polling = true;
        // ~20 minutes. The old cap was ~6, and a cross-app task that reads a
        // note and then prices on another app legitimately runs that long: the
        // app gave up while the agent was still working, told the user "超时未
        // 返回", and the answer never appeared even though the run finished.
        if (attempt > 1600) {
            polling = false;
            ui(() -> {
                append("PhoneGo", "这条任务跑了 20 分钟以上，我先停止等待了。"
                        + "它可能还在后台跑，可以稍后在「地图/用量」里看结果。");
                setBusy(false);
            });
            return;
        }
        io.execute(() -> {
            try {
                Thread.sleep(700);
                HttpURLConnection conn = connect("/task/" + id);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(15000);
                JSONObject state = new JSONObject(read(conn));
                lastPollAt = System.currentTimeMillis();
                String status = state.optString("state", "?");
                String answer = state.optString("answer", "");
                String question = state.optString("question", "");
                String frame = state.optString("frame", "");
                JSONArray steps = state.optJSONArray("steps");

                long nowMs = System.currentTimeMillis();
                if (!frame.isEmpty() && !frame.equals(lastFrame) && nowMs - lastFrameAt > 1200) {
                    lastFrame = frame;
                    lastFrameAt = nowMs;
                    byte[] png = Base64.decode(frame, Base64.DEFAULT);
                    Bitmap bitmap = BitmapFactory.decodeByteArray(png, 0, png.length);
                    ui(() -> {
                        // drop the previous picture before keeping a new one:
                        // a 1280x2772 bitmap every second is 14 MB
                        android.graphics.drawable.Drawable old = frameView.getDrawable();
                        frameView.setImageBitmap(bitmap);
                        if (capsuleFrame != null && capsule != null) {
                            capsuleFrame.setImageBitmap(bitmap);
                            capsule.setVisibility(View.VISIBLE);
                        }
                        if (old instanceof android.graphics.drawable.BitmapDrawable) {
                            android.graphics.Bitmap previous =
                                    ((android.graphics.drawable.BitmapDrawable) old).getBitmap();
                            if (previous != null && previous != bitmap && !previous.isRecycled()) {
                                previous.recycle();
                            }
                        }
                        frameCard.setVisibility(View.VISIBLE);
                        // The capsule already shows the picture, so this only
                        // needs to be a way in. Keeping 280dp of the same
                        // frames in the chat is duplication, not information.
                        // The mirror is always in the capsule now (a view in
                        // this window), so a second copy above the input is
                        // pure duplication -- and it came back the moment the
                        // old system-overlay preference was cleared.
                        frameView.setVisibility(View.GONE);
                        frameCard.setVisibility(View.GONE);
                        if (frameDialog != null && bigFrame != null) {
                            bigFrame.setImageBitmap(bitmap);
                        }
                        frameLabel.setText(getString(R.string.frame_step, shownSteps));
                    });
                }

                // new steps only: the server mirrors the agent's list, so the
                // first N entries have already been shown
                int total = steps == null ? 0 : steps.length();
                if (total > shownSteps) {
                    StringBuilder sb = new StringBuilder();
                    for (int i = shownSteps; i < total; i++) {
                        JSONObject step = steps.optJSONObject(i);
                        // the answer arrives as its own bubble; printing it a
                        // second time as a step reads like a duplicate
                        if ("finish".equals(step.optString("kind"))) {
                            continue;
                        }
                        String kind = step.optString("kind");
                        String line = describeStep(
                                kind,
                                step.optString("action"),
                                step.optString("detail"));
                        if (!line.isEmpty()) {
                            // ◆ marks a replay from the map: the only step in
                            // the transcript that costs no model round. It is
                            // drawn in the accent colour, so "what did the map
                            // actually save" is visible without reading.
                            sb.append("macro".equals(kind) ? "◆ " : "· ")
                                    .append(line).append("\n");
                        }
                    }
                    if (sb.length() == 0) {
                        shownSteps = total;
                        return;
                    }
                    final boolean firstBatch = shownSteps == 0;
                    shownSteps = total;
                    final String chunk = sb.toString().trim();
                    // one header per task, not one per batch: the steps read as
                    // a single block while they arrive
                    ui(() -> {
                        appendLines(chunk);
                    });
                }

                if (!question.isEmpty() && !id.equals(awaitingTaskId)) {
                    ui(() -> {
                        awaitingTaskId = id;
                        notifyNeedsYou(question);
                        append("PhoneGo", "需要你回答：" + question);
                        statusText.setText("等待你的回答…");
                        // the send button is the reply channel, so it has to be
                        // usable even though a task is running
                        sendButton.setEnabled(true);
                        sendButton.setAlpha(1f);
                        input.setHint("回复 PhoneGo");
                    });
                }

                if ("done".equals(status) || "failed".equals(status)
                        || "partial".equals(status) || "cancelled".equals(status)) {
                    final boolean ok = "done".equals(status);
                    final boolean stopped = "cancelled".equals(status);
                    final boolean part = "partial".equals(status);
                    int replays = 0;
                    JSONArray allSteps = state.optJSONArray("steps");
                    if (allSteps != null) {
                        for (int i = 0; i < allSteps.length(); i++) {
                            JSONObject one = allSteps.optJSONObject(i);
                            if (one != null && "macro".equals(one.optString("kind"))) {
                                replays++;
                            }
                        }
                    }
                    final String spend = spentLine(state.optJSONObject("usage"), replays);
                    polling = false;
                    lastPollAt = System.currentTimeMillis();
                    boolean alreadyShown = id.equals(answeredTaskId);
                    answeredTaskId = id;
                    currentTaskId = "";
                    if (alreadyShown) {
                        return;          // the answer is on screen; do not repeat it
                    }
                    // A task that ran on the user's own screen leaves its app
                    // in front there -- they asked a question and got 小红书
                    // back. Unless the instruction asked to be left on that
                    // screen, the chat comes forward again.
                    if (state.optBoolean("on_user_display") && !state.optBoolean("leave_app")) {
                        ui(this::returnToChat);
                    }
                    ui(() -> {
                        append("PhoneGo", stopped ? "已取消"
                                : (ok ? "完成：" : (part ? "部分完成：" : "失败：")) + answer);
                        // what this instruction cost, right under its own answer
                        if (!spend.isEmpty()) {
                            note(spend);
                        }
                        setBusy(false);
                    });
                    return;
                }

                final int stepCount = shownSteps;
                ui(() -> {
                    // a pending question owns the status line; do not overwrite it
                    if (statusText != null && statusRow.getVisibility() == View.VISIBLE
                            && awaitingTaskId.isEmpty()) {
                        statusText.setText(stepCount > 0
                                ? "执行中… 已完成 " + stepCount + " 步"
                                : "执行中…");
                    }
                });
                ui(() -> poll(id, attempt + 1));
            } catch (Throwable e) {
                // Throwable, not Exception: an OutOfMemoryError used to kill
                // this thread silently, leaving the task "running" on screen
                // long after it had finished.
                polling = false;
                ui(() -> {
                    append("PhoneGo", "轮询失败：" + e);
                    setBusy(false);
                });
            }
        });
    }

    /**
     * One task's token spend, in the terms a person reads.
     *
     * Shown under the answer it belongs to rather than in the usage dialog,
     * because "what did this one instruction cost" is the question people
     * actually ask.
     */
    private static String spentLine(JSONObject usage, int replays) {
        if (usage == null) {
            return "";
        }
        int calls = usage.optInt("calls");
        int prompt = usage.optInt("prompt");
        int completion = usage.optInt("completion");
        if (calls <= 0) {
            return "";
        }
        String line = "本次 " + calls + " 次调用 · 输入 " + compact(prompt)
                + " · 输出 " + compact(completion) + " token";
        if (replays > 0) {
            // the point of the map, spelled out: these steps cost no call
            line += " · 地图回放 " + replays + " 次";
        }
        return line;
    }

    /** 19268 -> 19.3k, so the line stays one line. */
    private static String compact(int value) {
        if (value < 1000) {
            return String.valueOf(value);
        }
        return String.format(java.util.Locale.US, "%.1fk", value / 1000.0);
    }

    private void cancelTask() {
        if (currentTaskId.isEmpty()) {
            return;
        }
        final String id = currentTaskId;
        statusText.setText("正在取消…");
        new Thread(() -> {
            try {
                HttpURLConnection conn =
                        connect("/task/" + id + "/cancel");
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.getOutputStream().close();
                read(conn);
            } catch (Exception e) {
                ui(() -> append("PhoneGo", "取消失败：" + e));
            }
        }).start();
    }

    /** Send the user's reply to a task that is waiting on ask_user. */
    private void answerTask(String id, String text) {
        statusText.setText("执行中…");
        new Thread(() -> {
            try {
                HttpURLConnection conn = connect("/task/" + id + "/answer");
                conn.setRequestMethod("POST");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(15000);
                conn.setRequestProperty("Content-Type", "application/json");
                JSONObject body = new JSONObject().put("text", text);
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                read(conn);
                ui(() -> {
                    sendButton.setEnabled(false);
                    sendButton.setAlpha(0.45f);
                    input.setHint(getString(R.string.hint));
                });
            } catch (Exception e) {
                ui(() -> append("PhoneGo", "提交回答失败：" + e));
            }
        }).start();
    }

    /** While a task runs the send button is off and the cancel button is on. */
    /**
     * Show the last picture the agent published, even if this app did not
     * start the task (a task can come from the API, or from before a restart).
     */
    private void loadLastFrame() {
        io.execute(() -> {
            HttpURLConnection conn = null;
            try {
                conn = connect("/frame");
                conn.setConnectTimeout(2000);
                conn.setReadTimeout(4000);
                if (conn.getResponseCode() != 200) {
                    return;                       // the finally closes it
                }
                java.io.InputStream in = conn.getInputStream();
                java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                byte[] buffer = new byte[8192];
                int read;
                while ((read = in.read(buffer)) > 0) {
                    out.write(buffer, 0, read);
                }
                // /frame serves the PNG itself, not base64 of it -- decoding it
                // as base64 produced garbage and an empty capsule
                byte[] png = out.toByteArray();
                conn.disconnect();
                if (png.length < 100) {         // 503 body, not an image
                    return;
                }
                ui(() -> {
                    hasFrame = true;
                    showCapsule(true);
                });
                android.graphics.Bitmap bitmap =
                        BitmapFactory.decodeByteArray(png, 0, png.length);
                if (bitmap == null) {
                    return;
                }
                ui(() -> {
                    if (capsuleFrame != null && capsule != null) {
                        capsuleFrame.setImageBitmap(bitmap);
                        capsule.setVisibility(View.VISIBLE);
                    }
                });
            } catch (Throwable ignored) {
                // no frame yet: the capsule simply stays hidden
            } finally {
                if (conn != null) conn.disconnect();
            }
        });
    }

    /**
     * Keeps the capsule showing the newest picture.
     *
     * A task started from the API (or one running before a restart) never goes
     * through this app's poll, so the mirror would sit on an old screenshot.
     */
    private final Runnable frameTicker = new Runnable() {
        @Override
        public void run() {
            if (isFinishing()) {
                return;
            }
            loadLastFrame();
            handler.postDelayed(this, 2500);
        }
    };

    /** Expanded shows the picture; collapsed is one dot you can shove aside. */
    private void applyCapsuleSize(boolean collapsed) {
        if (capsule == null) {
            return;
        }
        capsuleCollapsed = collapsed;
        android.view.ViewGroup.LayoutParams params = capsule.getLayoutParams();
        // Smaller than the first version: 96x200dp mirrors a 1:2.08 screen
        // without covering a chat bubble's worth of text.
        params.width = dp(collapsed ? 38 : 96);
        params.height = dp(collapsed ? 38 : 200);
        capsule.setLayoutParams(params);
        if (capsuleFrame != null) {
            capsuleFrame.setVisibility(collapsed ? View.GONE : View.VISIBLE);
        }
        if (capsuleDot != null) {
            capsuleDot.setVisibility(collapsed ? View.VISIBLE : View.GONE);
        }
        android.widget.TextView label = findViewById(R.id.capsuleLabel);
        if (label != null) {
            label.setVisibility(collapsed ? View.GONE : View.VISIBLE);
        }
        getSharedPreferences("agent", MODE_PRIVATE).edit()
                .putBoolean("capsule_collapsed", collapsed).apply();
        capsule.post(() -> {
            if (capsule.getParent() != null) {
                android.view.View parent = (android.view.View) capsule.getParent();
                capsule.setX(clamp(capsule.getX(), 0,
                        Math.max(0, parent.getWidth() - capsule.getWidth())));
                capsule.setY(clamp(capsule.getY(), 0,
                        Math.max(0, parent.getHeight() - capsule.getHeight())));
                snapCapsuleToEdge(false);
                saveCapsulePosition();
            }
        });
    }

    /** Where the user last left it, as a fraction of the window. */
    private void saveCapsulePosition() {
        if (capsule == null || capsule.getParent() == null) {
            return;
        }
        android.view.View parent = (android.view.View) capsule.getParent();
        if (parent.getWidth() == 0 || parent.getHeight() == 0) {
            return;
        }
        getSharedPreferences("agent", MODE_PRIVATE).edit()
                .putFloat("capsule_x", capsule.getX() / parent.getWidth())
                .putFloat("capsule_y", capsule.getY() / parent.getHeight())
                .apply();
    }

    private void restoreCapsulePosition() {
        if (capsule == null || capsule.getParent() == null) {
            return;
        }
        android.content.SharedPreferences prefs =
                getSharedPreferences("agent", MODE_PRIVATE);
        if (!prefs.contains("capsule_x")) {
            return;                       // never dragged: the layout's corner is fine
        }
        android.view.View parent = (android.view.View) capsule.getParent();
        float x = prefs.getFloat("capsule_x", 0f) * parent.getWidth();
        float y = prefs.getFloat("capsule_y", 0f) * parent.getHeight();
        // A position saved before this rule existed can be on top of the input
        // row; clamping on the way in fixes it without a migration.
        capsule.setX(clamp(x, 0, parent.getWidth() - capsule.getWidth()));
        capsule.setY(clamp(y, capsuleTop(), capsuleBottom()));
    }

    /**
     * Snap to the nearer side, like a phone's assistive touch.
     *
     * Free placement means it always ends up over something; hugging an edge
     * costs the least room and is where the hand expects to find it again.
     */
    private void snapCapsuleToEdge(boolean animate) {
        if (capsule == null || capsule.getParent() == null) {
            return;
        }
        android.view.View parent = (android.view.View) capsule.getParent();
        if (parent.getWidth() == 0) {
            return;
        }
        float margin = dp(2);
        float center = capsule.getX() + capsule.getWidth() / 2f;
        float targetX = center < parent.getWidth() / 2f
                ? margin
                : parent.getWidth() - capsule.getWidth() - margin;
        float targetY = clamp(capsule.getY(), 0,
                parent.getHeight() - capsule.getHeight());
        if (animate) {
            capsule.animate().x(targetX).y(targetY).setDuration(160).start();
        } else {
            capsule.setX(targetX);
            capsule.setY(targetY);
        }
    }

    private static float clamp(float value, float low, float high) {
        return value < low ? low : (value > high ? high : value);
    }

    /**
     * Drag to move, tap to fold or unfold.
     *
     * A touch listener rather than a click listener because both gestures start
     * the same way: the movement decides which one it was.
     */
    private final class CapsuleTouch implements android.view.View.OnTouchListener {
        private float startX;
        private float startY;
        private float startRawX;
        private float startRawY;
        private boolean moved;
        private boolean folded;
        private final Runnable fold = () -> {
            folded = true;
            android.view.View view = capsule;
            if (view != null) {
                view.setAlpha(1f);
                toggleCapsule();
            }
        };

        @Override
        public boolean onTouch(android.view.View view, android.view.MotionEvent event) {
            switch (event.getActionMasked()) {
                case android.view.MotionEvent.ACTION_DOWN:
                    startX = view.getX();
                    startY = view.getY();
                    startRawX = event.getRawX();
                    startRawY = event.getRawY();
                    moved = false;
                    folded = false;
                    view.setAlpha(0.85f);
                    // the view consumes the gesture, so a long press has to be
                    // timed here -- a click listener never sees it
                    handler.postDelayed(fold, 500);
                    return true;
                case android.view.MotionEvent.ACTION_MOVE:
                    float dx = event.getRawX() - startRawX;
                    float dy = event.getRawY() - startRawY;
                    if (Math.abs(dx) > dp(6) || Math.abs(dy) > dp(6)) {
                        if (!moved) {
                            handler.removeCallbacks(fold);
                        }
                        moved = true;
                    }
                    if (moved && view.getParent() != null) {
                        android.view.View parent = (android.view.View) view.getParent();
                        view.setX(clamp(startX + dx, 0, parent.getWidth() - view.getWidth()));
                        view.setY(clamp(startY + dy, capsuleTop(), capsuleBottom()));
                    }
                    return true;
                case android.view.MotionEvent.ACTION_UP:
                case android.view.MotionEvent.ACTION_CANCEL:
                    view.setAlpha(1f);
                    handler.removeCallbacks(fold);
                    if (folded) {
                        folded = false;              // the long press already acted
                    } else if (moved) {
                        snapCapsuleToEdge(true);
                        // the animation moves it after the fact, so give the
                        // saving a moment to see the settled position
                        handler.postDelayed(MainActivity.this::saveCapsulePosition, 220);
                    } else if (event.getActionMasked() == android.view.MotionEvent.ACTION_UP) {
                        // Tell accessibility services a click happened: the view
                        // eats the gesture, so nothing else would report it.
                        view.performClick();
                        if (capsuleCollapsed) {
                            applyCapsuleSize(false);
                            restoreCapsulePosition();
                        } else {
                            showFrameViewer();
                        }
                    }
                    return true;
                default:
                    return true;
            }
        }
    }

    /**
     * The mirror steps aside while the keyboard is up.
     *
     * With the keyboard open the window is less than three quarters of the
     * screen; the capsule then has nowhere to go that is not on top of the
     * conversation, so it hides until the keyboard closes.
     */
    private void onRootLayout() {
        if (capsule == null || capsule.getParent() == null) {
            return;
        }
        android.view.View root = (android.view.View) capsule.getParent();
        int height = root.getHeight();
        if (height <= 0) {
            return;
        }
        int screen = getResources().getDisplayMetrics().heightPixels;
        boolean keyboard = height < screen * 3 / 4;
        capsule.setVisibility(keyboard || !hasFrame ? View.GONE : View.VISIBLE);
        if (!keyboard) {
            clampCapsule();
        }
    }

    /** Below the title row: the capsule must not sit on the header. */
    private int capsuleTop() {
        return dp(76);
    }

    /** Above the input row: covering the send button makes the app unusable. */
    private int capsuleBottom() {
        android.view.View parent = capsule == null ? null : (android.view.View) capsule.getParent();
        int height = parent == null ? 0 : parent.getHeight();
        if (height <= 0) {
            return Math.max(capsuleTop(), getResources().getDisplayMetrics().heightPixels - dp(260));
        }
        return Math.max(capsuleTop(), height - dp(120) - (capsule == null ? 0 : capsule.getHeight()));
    }

    /** Keep the capsule inside the part of the window that has no controls. */
    private void clampCapsule() {
        if (capsule == null || capsule.getParent() == null) {
            return;
        }
        android.view.View parent = (android.view.View) capsule.getParent();
        int maxX = Math.max(0, parent.getWidth() - capsule.getWidth());
        capsule.setX(clamp(capsule.getX(), 0, maxX));
        capsule.setY(clamp(capsule.getY(), capsuleTop(), capsuleBottom()));
    }

    /** Long-press on the capsule folds it; the dot folds back out. */
    private void toggleCapsule() {
        applyCapsuleSize(!capsuleCollapsed);
        restoreCapsulePosition();
        clampCapsule();
    }

    /** The in-app capsule: visible whenever there is a picture to show. */
    private void showCapsule(boolean visible) {
        if (capsule != null) {
            capsule.setVisibility(visible ? View.VISIBLE : View.GONE);
        }
        // The mirror floats over the transcript. Without room on the right the
        // answer bubble slides under it and a sentence is cut in half -- the
        // capsule is 96dp wide plus its margin, so the chat keeps that much.
        if (messages != null) {
            int pad = (visible && !capsuleCollapsed) ? dp(104) : 0;
            messages.setPadding(messages.getPaddingLeft(), messages.getPaddingTop(),
                    pad, messages.getPaddingBottom());
        }
    }

    private void hideKeyboard() {
        android.view.View focus = getCurrentFocus();
        android.view.inputmethod.InputMethodManager imm =
                (android.view.inputmethod.InputMethodManager)
                        getSystemService(INPUT_METHOD_SERVICE);
        if (focus != null && imm != null) {
            imm.hideSoftInputFromWindow(focus.getWindowToken(), 0);
        }
    }

    //: The bubble that sits in the chat while a task runs. It is replaced by the
    //: first real message, so the transcript never claims something it cannot.
    private android.view.View thinkingRow;
    private android.widget.TextView thinkingText;
    private int thinkingDots;
    private final Runnable thinkingTick = new Runnable() {
        @Override
        public void run() {
            if (thinkingText == null) {
                return;
            }
            thinkingDots = (thinkingDots + 1) % 4;
            StringBuilder dots = new StringBuilder("正在操作手机");
            for (int i = 0; i < thinkingDots; i++) {
                dots.append("·");
            }
            thinkingText.setText(dots.toString());
            handler.postDelayed(this, 450);
        }
    };

    private void showThinking() {
        if (thinkingRow != null || messages == null) {
            return;
        }
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(android.view.Gravity.START);

        TextView avatar = new TextView(this);
        avatar.setText("✦");
        avatar.setTextSize(13f);
        avatar.setTextColor(getColor(R.color.white));
        avatar.setGravity(android.view.Gravity.CENTER);
        avatar.setBackground(getDrawable(R.drawable.bg_avatar));
        LinearLayout.LayoutParams avatarParams =
                new LinearLayout.LayoutParams(dp(30), dp(30));
        avatarParams.rightMargin = dp(8);
        avatarParams.topMargin = dp(2);
        row.addView(avatar, avatarParams);

        thinkingText = new TextView(this);
        thinkingText.setText("正在操作手机");
        thinkingText.setTextSize(14f);
        thinkingText.setTextColor(getColor(R.color.ink_soft));
        thinkingText.setBackground(getDrawable(R.drawable.bg_agent));
        thinkingText.setPadding(dp(14), dp(10), dp(14), dp(10));
        row.addView(thinkingText);

        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(8);
        messages.addView(row, params);
        thinkingRow = row;
        thinkingDots = 0;
        closeStepBlock();
        scrollDown();
        handler.post(thinkingTick);
    }

    private void hideThinking() {
        handler.removeCallbacks(thinkingTick);
        if (thinkingRow != null && messages != null) {
            messages.removeView(thinkingRow);
        }
        thinkingRow = null;
        thinkingText = null;
    }

    private void setBusy(boolean busy) {
        if (!busy) {
            hideThinking();
        }
        if (statusRow == null) {
            return;
        }
        statusRow.setVisibility(busy ? View.VISIBLE : View.GONE);
        statusText.setText(getString(R.string.running));
        sendButton.setEnabled(!busy);
    }


    private static String read(HttpURLConnection conn) throws Exception {
        try {
            InputStream in = conn.getResponseCode() >= 400
                    ? conn.getErrorStream() : conn.getInputStream();
            StringBuilder sb = new StringBuilder();
            try (BufferedReader reader =
                         new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    sb.append(line);
                }
            }
            return sb.toString();
        } finally {
            // Every poll opens a new connection; leaving them to the pool is how
            // a phone ends up with a socket leak after an hour of tasks.
            conn.disconnect();
        }
    }

    private void ui(Runnable action) {
        handler.post(action);
    }

    /** System chatter: centred, small, no bubble -- it is not the agent talking. */
    /**
     * A message the user has to see, welcome screen or not.
     *
     * note() deliberately stays out of the welcome composition; a failed
     * reconnect is not decoration, so it gets a bubble of its own.
     */
    private void alert(String text) {
        if (welcome != null && welcome.getVisibility() == View.VISIBLE) {
            append("PhoneGo", text);
        } else {
            note(text);
        }
    }

    private void note(String text) {
        if (welcome != null && welcome.getVisibility() == View.VISIBLE) {
            // The welcome screen is a deliberate composition; a status line
            // dropping into the middle of it looks like part of the artwork.
            return;
        }
        TextView line = new TextView(this);
        line.setText(text.trim());
        line.setTextSize(12f);
        line.setTextColor(getColor(R.color.ink_soft));
        line.setGravity(android.view.Gravity.CENTER);
        line.setPadding(dp(24), dp(6), dp(24), dp(6));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(6);
        closeStepBlock();
        messages.addView(line, params);
        trimMessages();
        // deliberately not remembered: startup chatter repeated on every launch
        // is noise, and the conversation is what should survive a restart
        scrollDown();
    }

    /**
     * Step lines, accumulated into one view instead of one per line.
     *
     * A 25-minute crawl prints a line every couple of seconds. Giving each its
     * own TextView built a transcript of a thousand views: the main thread then
     * spent its whole time on layout, taps went unanswered and polling stopped
     * -- the app looked frozen while the agent was still working. One growing
     * block, capped, keeps that from happening.
     */
    private void appendLines(String text) {
        hideThinking();
        if (stepBlock == null || stepBlock.getParent() == null) {
            stepLines.clear();               // a new block starts a new list
            stepBlock = new TextView(this);
            stepBlock.setTextSize(12.5f);
            stepBlock.setTextColor(getColor(R.color.ink_soft));
            stepBlock.setLineSpacing(6f, 1f);
            stepBlock.setPadding(dp(14), dp(6), dp(14), dp(6));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            params.leftMargin = dp(44);      // line up under the bubble, past the avatar
            params.bottomMargin = dp(4);
            messages.addView(stepBlock, params);
        }
        text = friendlyStep(text);
        if (text.isEmpty()) {
            return;
        }
        if (!crawlId.isEmpty()) {
            // There is no per-line button in the transcript, so the progress
            // block itself is the control while a crawl runs. Killing the app
            // used to be the only way to stop one.
            stepBlock.setOnClickListener(v -> cancelCrawl());
            stepBlock.setTextColor(getColor(R.color.accent));
        } else {
            stepBlock.setOnClickListener(null);
            stepBlock.setTextColor(getColor(R.color.ink_soft));
        }
        // Kept as lines so a replayed step can be styled: a plain String cannot
        // hold a colour, and rebuilding the block is cheap next to one layout.
        for (String line : text.split("\n")) {
            if (!line.trim().isEmpty()) {
                stepLines.add(line.trim());
            }
        }
        while (stepLines.size() > 80) {              // keep the tail, drop the head
            stepLines.remove(0);
        }
        android.text.SpannableStringBuilder styled = new android.text.SpannableStringBuilder();
        for (String line : stepLines) {
            int start = styled.length();
            styled.append(line).append("\n");
            if (line.startsWith("◆")) {
                styled.setSpan(new android.text.style.ForegroundColorSpan(getColor(R.color.accent)),
                        start, start + line.length(),
                        android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                styled.setSpan(new android.text.style.StyleSpan(android.graphics.Typeface.BOLD),
                        start, start + line.length(),
                        android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            }
        }
        stepBlock.setText(styled);
        trimMessages();
        scrollDown();
    }

    /**
     * Is this crawl line worth putting in the conversation?
     *
     * Screens found, screens named, warnings, the end -- those move the story.
     * Enrichment counts, launch retries and dumps do not.
     */
    /** "… (176.1s)" -> "176 秒"; empty when the line carries no timing. */
    /**
     * A step as a sentence instead of a log fragment.
     *
     * "蓝牙 macro ok (3/3 steps)" is what the executor reports; what happened is
     * that it walked a known path to the Bluetooth page. The plan line is an
     * English instruction to the model, so it is dropped rather than shown.
     */
    /** Screen ids ("wlan_wlan") are not names; a person needs none of them. */
    private static boolean looksLikeId(String text) {
        return text != null && text.matches("^[a-z0-9_]+$");
    }

    /**
     * One step, in words a person can follow.
     *
     * The server sends {kind: vlm, action: launch, detail: ok}; printing that
     * verbatim gives "launch ok" over and over, which says nothing about what
     * the agent is doing.
     */
    private static String describeStep(String kind, String action, String detail) {
        String what = action == null ? "" : action.trim();
        String how = detail == null ? "" : detail.trim();
        if ("route".equals(kind)) {
            return "判断：这一步用「" + how + "」";
        }
        if ("escaped".equals(kind)) {
            String app = what.isEmpty() ? "App" : what;
            return "「" + app + "」跑到了你自己的屏幕上";
        }
        if ("follow".equals(kind)) {
            String app = what.isEmpty() ? "App" : what;
            return "跟着「" + app + "」到你自己的屏幕上继续";
        }
        if ("type".equals(kind)) {
            // the search-query step the macro layer types by itself
            return what.isEmpty() ? "输入文字" : "输入「" + what + "」";
        }
        if ("pull".equals(kind)) {
            // the detail is "moved back to the agent's display": what happened,
            // in words a person reading the chat would use
            String app = what.isEmpty() ? "目标 App" : what;
            return "「" + app + "」跳到了你的屏幕上，已搬回虚拟屏";
        }
        if ("verify".equals(kind)) {
            if ("mismatch".equals(what)) {
                return "当前页面和地图记录的不一样，按眼前的页面继续";
            }
            if ("refused".equals(what)) {
                return "模型没看到页面，换一屏继续看";
            }
            return "核对页面内容";
        }
        if ("shade".equals(kind)) {
            return "下拉通知栏挡住了界面，已收起";
        }
        if ("memory".equals(kind)) {
            // the detail is the fact itself; "remembered" is the verb
            return how.isEmpty() ? "记住了屏上的一条信息" : "记住了：" + how;
        }
        if ("macro".equals(kind)) {
            String name = what.isEmpty() ? "已知路径" : what;
            if (how.contains("steps")) {
                java.util.regex.Matcher m = java.util.regex.Pattern
                        .compile("\\(([0-9]+)/([0-9]+) steps\\)").matcher(how);
                if (m.find()) {
                    return "地图回放：进入「" + cleanLabel(name) + "」的 " + m.group(1) + " 个动作";
                }
            }
            return "地图回放：进入「" + cleanLabel(name) + "」" + (how.contains("ok") ? "（完成）" : "");
        }
        if ("vlm".equals(kind) || "tap".equals(kind) || "act".equals(kind)) {
            if ("launch".equals(what)) {
                return "打开 App";
            }
            if ("macro".equals(what)) {
                return "沿已知路径操作";
            }
            if ("tap_index".equals(what)) {
                return "点按截图上的编号";
            }
            if ("tap".equals(what)) {
                return "点按屏幕";
            }
            if ("scroll".equals(what) || "swipe".equals(what)) {
                return "滚动页面";
            }
            if ("wait".equals(what)) {
                return "等待界面加载";
            }
            if ("back".equals(what)) {
                return "返回上一页";
            }
            if ("type".equals(what)) {
                return "输入文字";
            }
            if ("tap_text".equals(what)) {
                return how.isEmpty() || "ok".equals(how)
                        ? "点按文字" : "点按「" + how + "」";
            }
            if ("shell".equals(what)) {
                // the detail is the command itself now, and a person reading
                // the chat deserves to see what was run on their phone
                return how.isEmpty() ? "执行命令" : "执行命令：" + how;
            }
            if ("finish".equals(what)) {
                return "";
            }
            return what.isEmpty() ? "" : what;
        }
        return what.isEmpty() ? "" : what + (how.isEmpty() || "ok".equals(how) ? "" : " " + how);
    }

    /** A label without the bullet the step renderer already adds. */
    private static String cleanLabel(String text) {
        String value = text == null ? "" : text.trim();
        while (value.startsWith("·") || value.startsWith("-") || value.startsWith("•")) {
            value = value.substring(1).trim();
        }
        return value;
    }

    private static String friendlyStep(String text) {
        String line = text == null ? "" : text.trim();
        if (line.isEmpty()) {
            return "";
        }
        java.util.regex.Matcher macro =
                java.util.regex.Pattern.compile("^(.*?)\\s+macro ok\\s*\\(([0-9]+)/([0-9]+) steps\\)")
                        .matcher(line);
        if (macro.find()) {
            String target = cleanLabel(macro.group(1));
            return looksLikeId(target)
                    ? "· 按已知路径操作（" + macro.group(2) + " 步）"
                    : "· 按已知路径进入「" + target + "」（" + macro.group(2) + " 步）";
        }
        if (line.endsWith("设置全量")) {
            String target = cleanLabel(line.replace("设置全量", ""));
            return looksLikeId(target) ? "· 使用已知的地图" : "· 使用「" + target + "」的地图";
        }
        // A plan sentence is the model talking to itself in English; the visible
        // step is the one after it.
        if (line.matches("^[A-Za-z ,;:'\\-]+$")) {
            return "";
        }
        return line.startsWith("· ") || line.startsWith("  ") ? line : "· " + line;
    }

    private static String spentSeconds(String line) {
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("\\(([0-9]+(?:\\.[0-9]+)?)s\\)").matcher(line == null ? "" : line);
        if (!m.find()) {
            return "";
        }
        return Math.round(Double.parseDouble(m.group(1))) + " 秒";
    }

    /**
     * A crawl line without its tracer packaging.
     *
     * "[Verified] depth=1 tap '推荐' (from screen_0)" is the log format; what a
     * person needs is "点开 推荐". Paths are dropped entirely -- where the map is
     * written is a detail for the 地图 page.
     */
    private static String friendlyCrawlLine(String line) {
        String text = line == null ? "" : line.trim();
        text = text.replaceAll("^\\[[A-Za-z]+\\]\\s*", "");
        text = text.replaceAll("/data/\\S+", "").trim();
        text = text.replaceAll("\\s{2,}", " ");
        if (text.startsWith("depth=")) {
            java.util.regex.Matcher m = java.util.regex.Pattern
                    .compile("tap '([^']+)'").matcher(text);
            return m.find() ? "点开 " + m.group(1) : "";
        }
        // Everything that is not a tap is a tracer line: a package name, a
        // display id, a count of elements. The user is watching screens being
        // opened, not reading a log.
        return "";
    }

    private static boolean isMilestone(String line) {
        String text = line.trim();
        if (text.isEmpty()) {
            return false;
        }
        if (text.startsWith("[Enrich]") || text.startsWith("[launch]")
                || text.startsWith("[Verified]") || text.startsWith("[crawl]")) {
            return false;
        }
        return text.contains("new screen") || text.contains("known screen")
                || text.contains("screen_") && text.contains("->")
                || text.contains("找到") || text.contains("完成") || text.contains("失败")
                || text.contains("注意") || text.contains("停止") || text.contains("abort")
                || text.contains("coverage") || text.contains("map ");
    }

    /** A new bubble ends the step block, so later steps start fresh under it. */
    private void closeStepBlock() {
        stepBlock = null;
    }

    /**
     * Empty the transcript without losing the welcome block.
     *
     * removeAllViews() detaches every child, welcome included, and the block
     * was never put back -- a new session showed a blank screen.
     */
    private void clearChat() {
        messages.removeAllViews();
        if (welcome != null) {
            messages.addView(welcome, 0);
        }
    }

    /** Keep the transcript bounded: old views cost layout time forever. */
    private void trimMessages() {
        if (messages == null) {
            return;
        }
        for (int i = 0; i < MAX_BUBBLES; i++) {
            if (messages.getChildCount() <= MAX_BUBBLES) {
                return;
            }
            // The welcome block lives at index 0 until the first message, and
            // removing it leaves an empty screen with no explanation.
            android.view.View first = messages.getChildAt(0);
            if (first == welcome) {
                return;
            }
            messages.removeViewAt(0);
        }
    }

    /**
     * One message, as a bubble.
     *
     * The user speaks on the right in the accent colour; the agent replies on
     * the left with a small avatar, the way a chat with a person looks. Before
     * this, everything was one TextView with "PhoneAgent" printed above it.
     */
    private void append(String who, String message) {
        boolean mine = "你".equals(who);
        if (!mine) {
            hideThinking();
        }
        if (mine && welcome != null && welcome.getVisibility() == View.VISIBLE) {
            welcome.setVisibility(View.GONE);
        }
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(mine ? android.view.Gravity.END : android.view.Gravity.START);

        if (!mine) {
            TextView avatar = new TextView(this);
            avatar.setText("✦");
            avatar.setTextSize(13f);
            avatar.setTextColor(getColor(R.color.white));
            avatar.setGravity(android.view.Gravity.CENTER);
            avatar.setBackground(getDrawable(R.drawable.bg_avatar));
            LinearLayout.LayoutParams avatarParams =
                    new LinearLayout.LayoutParams(dp(30), dp(30));
            avatarParams.rightMargin = dp(8);
            avatarParams.topMargin = dp(2);
            row.addView(avatar, avatarParams);
        }

        TextView bubble = new TextView(this);
        bubble.setText(message);
        bubble.setTextSize(15f);
        bubble.setLineSpacing(6f, 1f);
        bubble.setTextIsSelectable(true);
        bubble.setTextColor(getColor(mine ? R.color.bubble_user_ink : R.color.ink));
        bubble.setBackground(getDrawable(mine ? R.drawable.bg_user : R.drawable.bg_agent));
        bubble.setPadding(dp(14), dp(10), dp(14), dp(10));
        bubble.setMaxWidth((int) (getResources().getDisplayMetrics().widthPixels * 0.76f));
        LinearLayout.LayoutParams bubbleParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        row.addView(bubble, bubbleParams);

        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setGravity(mine ? android.view.Gravity.END : android.view.Gravity.START);
        column.addView(row);

        TextView stamp = new TextView(this);
        stamp.setText(hhmm(System.currentTimeMillis()));
        stamp.setTextSize(10f);
        stamp.setTextColor(getColor(R.color.ink_soft));
        stamp.setPadding(dp(4), dp(2), dp(4), 0);
        column.addView(stamp);

        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rowParams.topMargin = dp(8);
        closeStepBlock();
        messages.addView(column, rowParams);
        trimMessages();
        remember(mine ? "you" : "agent", message);
        scrollDown();
    }

    // -- the conversation outlives the activity ---------------------------

    /** One message as it will be restored: kind plus text. */
    private final java.util.List<org.json.JSONObject> conversation = new java.util.ArrayList<>();
    private boolean restoring;

    // -- sessions ----------------------------------------------------------
    //
    // Several conversations with the same phone. Each owns its transcript;
    // they share the device, so a task belongs to the session that started it
    // and the others simply do not see it.

    /** The conversation being shown. Empty until the store is read. */
    private String sessionId = "";

    private java.io.File sessionFile() {
        return new java.io.File(getFilesDir(), "session-" + sessionId + ".json");
    }

    private java.io.File sessionIndex() {
        return new java.io.File(getFilesDir(), "sessions.json");
    }

    /** [{id, title, updated}] newest first. */
    private org.json.JSONArray readSessions() {
        try {
            if (!sessionIndex().exists()) {
                return new org.json.JSONArray();
            }
            return new org.json.JSONArray(readText(sessionIndex()));
        } catch (Throwable t) {
            return new org.json.JSONArray();
        }
    }

    private void writeSessions(org.json.JSONArray sessions) {
        try (java.io.FileWriter writer = new java.io.FileWriter(sessionIndex())) {
            writer.write(sessions.toString());
        } catch (Throwable t) {
            android.util.Log.w("Pairing", "could not save the session list: " + t);
        }
    }

    /** Create (or pick up) the session the app should show at startup. */
    private void ensureSession() {
        org.json.JSONArray sessions = readSessions();
        if (sessions.length() > 0) {
            org.json.JSONObject first = sessions.optJSONObject(0);
            sessionId = first == null ? "" : first.optString("id");
        }
        if (sessionId == null || sessionId.isEmpty()) {
            sessionId = newSession();
        }
    }

    /** A fresh conversation, made the current one. */
    private String newSession() {
        String id = "s" + System.currentTimeMillis();
        org.json.JSONArray sessions = readSessions();
        try {
            org.json.JSONObject entry = new org.json.JSONObject();
            entry.put("id", id);
            entry.put("title", "新会话");
            entry.put("updated", System.currentTimeMillis());
            org.json.JSONArray next = new org.json.JSONArray();
            next.put(entry);
            for (int i = 0; i < sessions.length(); i++) {
                next.put(sessions.get(i));
            }
            writeSessions(next);
        } catch (org.json.JSONException ignored) {
            // a session that cannot be indexed still works in memory
        }
        sessionId = id;
        return id;
    }

    /** Keep the list's title and order in step with what was said. */
    private void touchSession(String firstLine) {
        org.json.JSONArray sessions = readSessions();
        org.json.JSONArray next = new org.json.JSONArray();
        boolean found = false;
        try {
            for (int i = 0; i < sessions.length(); i++) {
                org.json.JSONObject entry = sessions.optJSONObject(i);
                if (entry == null) {
                    continue;
                }
                if (sessionId.equals(entry.optString("id"))) {
                    entry.put("updated", System.currentTimeMillis());
                    if (!firstLine.isEmpty() && "新会话".equals(entry.optString("title"))) {
                        entry.put("title", firstLine.length() > 18
                                ? firstLine.substring(0, 18) + "…" : firstLine);
                    }
                    found = true;
                }
                if (!found) {
                    next.put(entry);          // keep it at the top
                }
            }
            if (!found) {
                org.json.JSONObject entry = new org.json.JSONObject();
                entry.put("id", sessionId);
                entry.put("title", firstLine.isEmpty() ? "新会话" : firstLine);
                entry.put("updated", System.currentTimeMillis());
                next.put(entry);
            }
            // the touched session goes first, everything else keeps its order
            org.json.JSONArray ordered = new org.json.JSONArray();
            for (int i = 0; i < next.length(); i++) {
                org.json.JSONObject entry = next.optJSONObject(i);
                if (entry != null && sessionId.equals(entry.optString("id"))) {
                    ordered.put(entry);
                }
            }
            for (int i = 0; i < next.length(); i++) {
                org.json.JSONObject entry = next.optJSONObject(i);
                if (entry != null && !sessionId.equals(entry.optString("id"))) {
                    ordered.put(entry);
                }
            }
            writeSessions(ordered);
        } catch (org.json.JSONException ignored) {
            // titles are cosmetic
        }
    }

    /** Switch to another conversation and rebuild the panel. */
    private void switchSession(String id) {
        if (id == null || id.isEmpty() || id.equals(sessionId)) {
            return;
        }
        saveNow();
        sessionId = id;
        conversation.clear();
        clearChat();
        stepBlock = null;
        if (welcome != null) {
            welcome.setVisibility(View.GONE);
        }
        restoreChat();
        touchSession("");
        refreshSubtitle();
    }

    /** Build the chat panel from the current session's file. */
    private void reloadSession() {
        conversation.clear();
        clearChat();
        stepBlock = null;
        restoreChat();
    }

    private void saveNow() {
        handler.removeCallbacks(saveChat);
        saveChat.run();
    }

    private void remember(String kind, String text) {
        if (restoring) {
            return;                        // replaying the file, not adding to it
        }
        try {
            conversation.add(new org.json.JSONObject().put("k", kind).put("t", text));
            if ("you".equals(kind)) {
                // the first thing said names the conversation
                touchSession(text);
                org.json.JSONArray list = readSessions();
                if (list.length() > 0) {
                    refreshSubtitle();
                }
            }
            while (conversation.size() > 200) {
                conversation.remove(0);
            }
            handler.removeCallbacks(saveChat);
            handler.postDelayed(saveChat, 600);
        } catch (org.json.JSONException ignored) {
            // a message that cannot be stored is not worth failing over
        }
    }

    private final Runnable saveChat = () -> {
        try {
            org.json.JSONArray array = new org.json.JSONArray();
            for (org.json.JSONObject item : conversation) {
                array.put(item);
            }
            java.io.File file = sessionFile();
            try (java.io.FileWriter writer = new java.io.FileWriter(file)) {
                writer.write(array.toString());
            }
        } catch (Throwable t) {
            android.util.Log.w("Pairing", "could not save the chat: " + t);
        }
    };

    /**
     * Put the previous conversation back.
     *
     * The transcript lived only in the view tree, so closing the app -- or the
     * system reclaiming it -- threw away everything that had been said.
     */
    private void restoreChat() {
        java.io.File file = sessionFile();
        if (!file.exists()) {
            return;
        }
        restoring = true;
        try {
            org.json.JSONArray array = new org.json.JSONArray(readText(file));
            boolean any = false;
            for (int i = 0; i < array.length(); i++) {
                org.json.JSONObject item = array.optJSONObject(i);
                if (item == null) {
                    continue;
                }
                String kind = item.optString("k");
                String text = item.optString("t");
                // a version of this app printed a finished task's answer every
                // few seconds; those repeats are in the stored transcript and
                // should not come back on every start
                if (text.matches("^display=\\d+ size=.*")) {
                    // A reconnect used to print its raw result
                    // ("display=367 size=1280x2772 dpi=520 app=..."). It is in
                    // stored transcripts; drop it instead of showing it again
                    // on every launch.
                    continue;
                }
                if (!"steps".equals(kind) && !conversation.isEmpty()) {
                    org.json.JSONObject before = conversation.get(conversation.size() - 1);
                    if (text.equals(before.optString("t")) && kind.equals(before.optString("k"))) {
                        continue;
                    }
                }
                conversation.add(item);
                if ("you".equals(kind)) {
                    append("你", text);
                    any = true;
                } else if ("note".equals(kind)) {
                    note(text);
                } else if ("steps".equals(kind)) {
                    appendLines(text);
                } else {
                    append("PhoneGo", text);
                    any = true;
                }
            }
            if (any && welcome != null) {
                welcome.setVisibility(View.GONE);
            }
        } catch (Throwable t) {
            android.util.Log.w("Pairing", "could not restore the chat: " + t);
        } finally {
            restoring = false;
        }
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density);
    }

    private void scrollDown() {
        scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
    }
}
