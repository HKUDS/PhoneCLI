package com.phoneagent.app;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Chinese-to-English for the app's own text.
 *
 * The Chinese literal is the key, so the English UI cannot drift from what was
 * measured in Chinese: if a label changes, its translation disappears instead of
 * lying. Set in Settings → 语言 / Language, or follow the system locale.
 */
final class Tr {

    private static final Map<String, String> EN = new HashMap<>();

    private static SharedPreferences prefs;

    private Tr() {
    }

    static void attach(Context context) {
        prefs = context.getSharedPreferences("agent", Context.MODE_PRIVATE);
    }

    static boolean english() {
        String mode = prefs == null ? "system" : prefs.getString("lang", "system");
        if ("en".equals(mode)) {
            return true;
        }
        if ("zh".equals(mode)) {
            return false;
        }
        return !Locale.getDefault().getLanguage().startsWith("zh");
    }

    /** The app's text in the current language; unknown strings pass through. */
    static String t(String zh) {
        String en = EN.get(zh);
        return en == null || !english() ? zh : en;
    }

    static {
        put("取消", "Cancel");
        put("构建地图", "Build map");
        put("执行中…", "Working…");
        put("你", "You");
        put("地图", "Maps");
        put("新会话", "New chat");
        put("删除", "Delete");
        put("关闭", "Close");
        put("缺失", "missing");
        put("会话", "Chats");
        put("开始", "Start");
        put(" · 还有 ", " · and ");
        put(" 的首页声明为 singleTask，", "'s home screen is declared singleTask, ");
        put("知道了", "Got it");
        put("取消失败：", "Could not cancel: ");
        put("正在操作手机", "Driving the phone");
        put("输入文字", "Type something");
        put("地图回放：进入「", "Map replay: go to \"");
        put(" 步）", " steps)");
        put("PhoneGo 正在启动", "PhoneGo is starting");
        put("无线调试未开启：adbd 没有监听端口。请到「设置 → 开发者选项 → 无线调试」打开", "Wireless debugging is off: adbd is not listening. Open Settings → Developer options → Wireless debugging");
        put("PhoneGo 无法在此设备上运行", "PhoneGo cannot run on this device");
        put("PhoneGo 运行中", "PhoneGo is running");
        put("正在取消任务", "Cancelling the task");
        put("悬浮窗显示中", "Floating view is showing");
        put("停止任务", "Stop the task");
        put("打开 PhoneAgent", "Open PhoneAgent");
        put("点这里回到 PhoneAgent", "Tap here to return to PhoneAgent");
        put("系统不允许悬浮窗直接打开 App", "The system does not let a floating view open the app");
        put("点通知栏回到 PhoneAgent", "Tap the notification to return to PhoneAgent");
        put("还没有配对无线调试，先完成一次配对。", "Wireless debugging is not paired yet. Pair it once first.");
        put("配对 ", "Pairing ");
        put("正在与手机配对（", "Pairing with the phone (");
        put("Python 已就绪：", "Python is ready: ");
        put("\napp_map 模块：", "\napp_map module: ");
        put("已加载", "loaded");
        put("可用", "ok");
        put("Python 启动失败：", "Python failed to start: ");
        put("时间未知", "time unknown");
        put(" 分钟前", " min ago");
        put(" 小时前", " h ago");
        put(" 天前", " d ago");
        put("＋ 新会话", "+ New chat");
        put("新会话 · 之前的内容留在「会话」里", "New chat · what you had stays under Chats");
        put("任务还在执行，等它结束再切换会话。", "A task is still running. Switch chats when it finishes.");
        put("删除当前会话", "Delete this chat");
        put("删除这个会话？", "Delete this chat?");
        put("只删聊天记录，手机上装的地图和任务结果不受影响。", "Only the chat log goes; maps and task results on the phone are untouched.");
        put("最多爬取屏幕数", "Max screens to crawl");
        put("时间预算（分钟）", "Time budget (minutes)");
        put("最大深度 · 从首页最多点几下", "Max depth · taps from the home screen");
        put("重爬 ", "Re-crawl ");
        put("删除这份地图？", "Delete this map?");
        put("\n\n只删手机上的这一份，删了就找不回来。", "\n\nOnly the copy on this phone goes, and there is no undo.");
        put("地图已更新", "Map updated");
        put("地图操作失败：", "Map action failed: ");
        put("没能读取地图列表：", "Could not read the map list: ");
        put("还没有地图。建一张之后，同一个 App 的操作会被记下来，下次直接走已知路径。", "No maps yet. Once one is built, the same app's steps are recorded and replayed next time.");
        put("落点是这张图的自测成绩：在地图上随机挑几条路径重放，看是否落到记录的页面。", "Landings grade the map against itself: a few paths are replayed at random to see if they end where they were recorded.");
        put("＋ 构建新地图", "+ Build a new map");
        put("正在使用", "In use");
        put("重新测落点", "Re-measure landings");
        put("删除地图", "Delete map");
        put("删除「", "Delete \"");
        put("」的地图？\n", "\"'s map?\n");
        put("这个 App 会重新变回没有地图的状态。", "This app goes back to having no map.");
        put("取消固定", "Unpin");
        put("固定使用", "Pin");
        put(" 屏 · ", " screens · ");
        put(" 条操作", " actions");
        put(" 个没走到", " not reached");
        put(" 张同包地图", " maps for this package");
        put("落点 4/4", "landings 4/4");
        put("还没测过落点", "landings not measured");
        put("没有可测的导航操作", "no navigation to measure");
        put("落点 ", "Landings ");
        put("（旧数据）", " (older data)");
        put("正在重新测量「", "Re-measuring \"");
        put("」的落点…", "\" landings…");
        put("没能开始测量：", "Could not start measuring: ");
        put("测量失败：", "Measurement failed: ");
        put("」落点 ", "\" landings ");
        put("操作失败：", "Action failed: ");
        put("正在读取可爬取的 App…", "Reading the apps that can be crawled…");
        put("本地 agent 还没有列出 App，稍后再试。", "The local agent has not listed any apps yet; try again shortly.");
        put("没能读取 App 列表：", "Could not read the app list: ");
        put("最多爬取屏幕数 · 地图里最多记多少屏", "Max screens · the most this map may record");
        put("时间预算（分钟）· 到点就收工", "Time budget (minutes) · stops when it runs out");
        put("最大深度 · 从首页最多点几下（1 = 只进首页列出的页面）", "Max depth · taps from the home screen (1 = only what the home screen lists)");
        put("先试一次", "Try once first");
        put("可能起不来。爬一次就知道：起不来会立刻停下并记住，", "It may not launch. One crawl tells you: it stops at once and remembers, ");
        put("下次标成 ⛔。", "and marks it ⛔ next time.");
        put("试", "Try");
        put("算了", "Never mind");
        put("这个 App 爬不了", "This app cannot be crawled");
        put("系统只会把它放回自己所在的屏幕，虚拟屏上起不来。\n\n", "The system only puts it back on its own display; it will not start on the virtual one.\n\n");
        put("换一个标着 ✅ 的 App。", "Pick an app marked ✅ instead.");
        put("开始为「", "Starting a map for \"");
        put("」建地图：最多 ", "\": up to ");
        put(" 屏、", " screens, ");
        put(" 分钟。", " minutes.");
        put("进度会一行行显示在这里，完成后可以在「地图」里看到它。", "Progress shows up here line by line; when it is done the map appears under Maps.");
        put("\n正在爬取（点这里取消）", "\nCrawling (tap to cancel)");
        put("没能开始：", "Could not start: ");
        put("正在停止爬取…（已爬到的东西会保留）", "Stopping the crawl… (what it reached is kept)");
        put("这个 App", "This app");
        put("」完成：", "\" finished: ");
        put(" 屏", " screens");
        put("地图已保存", "Map saved");
        put("，用时 ", ", took ");
        put("。\n已保存，可以在「地图」里查看或固定使用。", ".\nSaved — see it under Maps, or pin it for use.");
        put("」没能完成：", "\" did not finish: ");
        put("需要你", "Needs you");
        put("PhoneGo 等你一下", "PhoneGo needs a moment from you");
        put("接续上次的任务…", "Resuming the last task…");
        put("用量", "Usage");
        put("读不到用量：", "Could not read usage: ");
        put("今天", "Today");
        put("近 ", "Last ");
        put(" 天", " days");
        put("每天消耗", "By day");
        put("按任务", "By task");
        put("按模型", "By model");
        put("Token 用量", "Token usage");
        put("收起", "Collapse");
        put("api base（例如 https://api.deepseek.com/v1）", "api base (e.g. https://api.deepseek.com/v1)");
        put("视觉模型（留空则与上面相同）", "Vision model (empty = same as above)");
        put("每条任务最多走几步（默认 35）", "Max steps per task (default 35)");
        put("DeepSeek 官方", "DeepSeek (official)");
        put("智谱", "Zhipu");
        put("阿里百炼", "Alibaba Bailian");
        put("硅基流动", "SiliconFlow");
        put("本地", "Local");
        put("测试连接", "Test connection");
        put("测试中…", "Testing…");
        put("✅ 可用：", "✅ Works: ");
        put("自主模式：花钱前也不问", "Autonomous: does not even ask before paying");
        put("诊断：查看运行环境", "Diagnostics: inspect the runtime");
        put("运行环境", "Runtime");
        put("尚未启动", "not started");
        put("\n\n心跳：", "\n\nheartbeat: ");
        put("立即重连", "Reconnect now");
        put("正在检查连接…", "Checking the connection…");
        put("模型设置", "Model settings");
        put("保存", "Save");
        put("配对无线调试", "Pair wireless debugging");
        put("还没有心跳记录", "no heartbeat yet");
        put(" 秒前（正常）", " s ago (ok)");
        put(" 秒前（可能卡住）", " s ago (may be stuck)");
        put("已清空密钥，agent 只能截图模式运行。", "Key cleared; the agent can only run in screenshot mode.");
        put("已保存，正在重启本地 agent…", "Saved, restarting the local agent…");
        put("保存失败：", "Could not save: ");
        put("还没有配置模型密钥。点「设置」填入 API Key，否则任务无法执行。", "No model key yet. Open Settings and paste an API key, or tasks cannot run.");
        put("✅ 无障碍服务已就绪（启动时由 App 自动恢复）。", "✅ Accessibility service is ready (the app restores it at launch).");
        put("提示：无障碍服务未开启。没有它，PhoneGo 无法读取配对码，", "Note: the accessibility service is off. Without it PhoneGo cannot read the pairing code, ");
        put("agent 也只能用截图（无法按文字标签点击）。", "and the agent can only use screenshots (no tapping by label).");
        put("\n长按「设置」可以直接打开无障碍设置页。", "\nLong-press Settings to open that page directly.");
        put("还没有可用的配对身份，请先配对无线调试。", "No pairing identity yet. Pair wireless debugging first.");
        put("无线调试当前是关闭的，PhoneGo 连不上自己的 adbd。", "Wireless debugging is off, so PhoneGo cannot reach its own adbd.");
        put("\n请到「设置 → 开发者选项 → 无线调试」打开开关，再回到这里。", "\nOpen Settings → Developer options → Wireless debugging, turn it on, then come back.");
        put("正在启动本地 agent（查找 adbd、建立虚拟屏）…", "Starting the local agent (finding adbd, creating the display)…");
        put("启动本地 agent 失败：\n", "Could not start the local agent:\n");
        put("系统设置", "System settings");
        put("无障碍服务", "Accessibility service");
        put("电池优化（后台运行）", "Battery optimisation (background)");
        put("导出备份（含 API Key）", "Export backup (includes the API key)");
        put("导入备份", "Import backup");
        put("导出备份", "Export backup");
        put("会把配对身份、爬好的地图、会话记录和模型设置打成一个 zip，", "Packs the pairing identity, the maps, the chats and the model settings into one zip, ");
        put("存到「下载」目录。\n\n", "saved to Downloads.\n\n");
        put("文件里的 API Key 是明文（它在本机是加密的，换机后无法解密），", "The API key inside is plain text (on this phone it is encrypted and cannot be decrypted elsewhere), ");
        put("请不要直接分享给别人。", "so do not hand the file to anyone.");
        put("导出", "Export");
        put("正在导出备份…", "Exporting…");
        put("备份已保存到「下载」：", "Backup saved to Downloads: ");
        put("导出失败：", "Export failed: ");
        put("打不开文件选择器：", "Could not open the file picker: ");
        put("正在导入备份…", "Importing…");
        put("已恢复 ", "Restored ");
        put(" 个文件。正在重启 agent…", " files. Restarting the agent…");
        put("导入失败：", "Import failed: ");
        put("已经不受电池优化限制，后台可以常驻。", "Battery optimisation is already off for PhoneGo; it can stay in the background.");
        put("打不开电池设置，请手动到「设置 → 应用 → PhoneGo → 省电策略 → 无限制」。", "Could not open battery settings; set Settings → Apps → PhoneGo → Battery saver → Unrestricted.");
        put("提示：无线调试是关闭的，adbd 没有监听端口。", "Note: wireless debugging is off and adbd is not listening.");
        put("\n请到「设置 → 开发者选项 → 无线调试」打开开关。", "\nOpen Settings → Developer options → Wireless debugging and turn it on.");
        put("提示：电池优化可能会在后台杀掉 PhoneGo。", "Note: battery optimisation may kill PhoneGo in the background.");
        put("\n长按「设置」→「电池优化」可以把它设为不限制。", "\nLong-press Settings → Battery optimisation to set it unrestricted.");
        put("需要先开启无障碍服务", "Turn on the accessibility service first");
        put("配对码只存在于系统的配对弹窗里，而且弹窗一关配对服务就停了，", "The pairing code only exists in the system dialog, and closing it stops the pairing service, ");
        put("所以只能由 PhoneGo 自己读取。\n\n", "so only PhoneGo itself can read it.\n\n");
        put("请到「设置 → 无障碍」开启 PhoneGo，再回来配对。", "Open Settings → Accessibility, enable PhoneGo, then pair again.");
        put("打开无障碍设置", "Open accessibility settings");
        put("请打开 设置 → 开发者选项 → 无线调试 → 使用配对码配对设备。", "Turn on Settings → Developer options → Wireless debugging → Pair device with pairing code.");
        put("\n配对码由 PhoneGo 自己读取，不用手输；弹出后两分钟内完成。", "\nPhoneGo reads the code itself; you do not type it. Finish within two minutes of the dialog appearing.");
        put("启动配对失败：\n", "Could not start pairing:\n");
        put("等待本地 agent 就绪…", "Waiting for the local agent…");
        put("任务没能提交：还没有配置模型密钥，点「设置」填入 API Key。\n", "The task was not submitted: no model key yet. Open Settings and paste one.\n");
        put("本地 agent 没有起来：\n", "The local agent did not start:\n");
        put("agent 还在启动，请稍等十几秒再试。", "The agent is still starting; give it ten seconds and try again.");
        put("PhoneGo 的后台进程卡住了（", "PhoneGo's background process is stuck (");
        put(" 秒没有心跳）。", " s without a heartbeat).");
        put("\n请从最近任务里划掉它，再重新打开一次。", "\nSwipe it away from Recents and open it again.");
        put("连接正常，不需要重连。", "The connection is fine; nothing to reconnect.");
        put("已重连，可以继续使用。", "Reconnected — carry on.");
        put("已自动重连，可以继续使用。", "Reconnected automatically — carry on.");
        put("重连失败：", "Reconnect failed: ");
        put("这条任务跑了 20 分钟以上，我先停止等待了。", "This task has been running over 20 minutes, so I stopped waiting.");
        put("它可能还在后台跑，可以稍后在「地图/用量」里看结果。", "It may still be running; the result shows up under Maps / Usage later.");
        put("需要你回答：", "Needs your answer: ");
        put("等待你的回答…", "Waiting for your answer…");
        put("回复 PhoneGo", "Reply to PhoneGo");
        put("已取消", "Cancelled");
        put("完成：", "Done: ");
        put("部分完成：", "Partly done: ");
        put("失败：", "Failed: ");
        put("执行中… 已完成 ", "Working… ");
        put(" 步", " steps");
        put("轮询失败：", "Polling failed: ");
        put("本次 ", "This run: ");
        put(" 次调用 · 输入 ", " calls · in ");
        put(" · 输出 ", " · out ");
        put(" · 地图回放 ", " · map replays ");
        put(" 次", "");
        put("正在取消…", "Cancelling…");
        put("提交回答失败：", "Could not send the answer: ");
        put("176 秒", "176 s");
        put("蓝牙 macro ok (3/3 steps)", "Bluetooth macro ok (3/3 steps)");
        put("判断：这一步用「", "Routing: this step uses \"");
        put("」跑到了你自己的屏幕上", "\" and landed on your own screen");
        put("跟着「", "Following \"");
        put("」到你自己的屏幕上继续", "\" onto your own screen");
        put("输入「", "Typing \"");
        put("目标 App", "Target app");
        put("」跳到了你的屏幕上，已搬回虚拟屏", "\" jumped to your screen; moved back to the virtual display");
        put("当前页面和地图记录的不一样，按眼前的页面继续", "The screen differs from the map; continuing from what is on it");
        put("模型没看到页面，换一屏继续看", "The model did not see the screen; taking another look");
        put("核对页面内容", "Checking the screen");
        put("下拉通知栏挡住了界面，已收起", "The notification shade was covering the screen; dismissed");
        put("记住了屏上的一条信息", "Noted one item from the screen");
        put("记住了：", "Noted: ");
        put("已知路径", "Known path");
        put("」的 ", "\": ");
        put(" 个动作", " actions");
        put("（完成）", " (done)");
        put("打开 App", "Open app");
        put("沿已知路径操作", "Follow the known path");
        put("点按截图上的编号", "Tap a number on the screenshot");
        put("点按屏幕", "Tap the screen");
        put("滚动页面", "Scroll");
        put("等待界面加载", "Wait for the screen");
        put("返回上一页", "Go back");
        put("点按文字", "Tap the text");
        put("点按「", "Tap \"");
        put("执行命令", "Run a command");
        put("执行命令：", "Run command: ");
        put("· 按已知路径操作（", "· follow the known path (");
        put("· 按已知路径进入「", "· follow the known path into \"");
        put("· 使用已知的地图", "· use the known map");
        put("· 使用「", "· use the map for \"");
        put("」的地图", "\"");
        put(" 秒", " s");
        put("点开 推荐", "Tap Recommended");
        put("点开 ", "Tap ");
        put("配对", "Pair");
        put("PhoneAgent 正在配对", "PhoneAgent is pairing");
        put("没有检测到配对弹窗（请在无线调试里打开「使用配对码配对设备」）", "No pairing dialog seen (turn on \"Pair device with pairing code\" in Wireless debugging)");
        put("，正在启动 agent", ", starting the agent");
        put("PhoneAgent 配对结果", "PhoneAgent pairing result");
        put("发消息给 PhoneGo…", "Message PhoneGo…");
        put("发送", "Send");
        put("设置", "Settings");
        put("虚拟屏画面", "Agent's screen");
        put("虚拟屏 · 点一下放大", "Agent's screen · tap to enlarge");
        put("手机上的 AI 助手", "An AI assistant on your phone");
        put("想让手机做什么？", "What should your phone do?");
        put("它会在后台的另一块屏上操作，你可以继续用手机", "It works on a separate screen in the background, so you keep using the phone");
        put("打开 WLAN 设置", "Open WLAN settings");
        put("查一下明天上海的天气", "Weather in Shanghai tomorrow");
        put("打开蓝牙设置", "Open Bluetooth settings");
        put("⚡ 自动", "⚡ Auto");
        put("👁 看图", "👁 Vision");
        put("怎么操作手机", "How to drive the phone");
        put("有地图走已知路径，没有就看屏幕", "With a map it follows the known path; otherwise it looks at the screen");
        put("不用地图，每一步都重新看屏幕", "No map: every step is decided from the screen");
        put("已切到自动：优先按已知路径，失败再看屏幕", "Auto: known paths first, screen when they fail");
        put("已切到看图：每一步都重新看屏幕", "Vision: every step is decided from the screen");
        put("用你的屏幕操作（托管）", "Drive your own screen (hands-off)");
        put("打开后会在你自己的屏幕上操作，你能看到每一步。京东、淘宝这类 App 只能这样跑。", "Operates on your screen so you can watch every step. Apps like JD or Taobao only run this way.");
        put("已打开托管：会在你自己的屏幕上操作", "Hands-off on: it operates on your own screen");
        put("已关闭托管：回到后台的虚拟屏", "Hands-off off: back to the background display");
        put("读取虚拟显示屏上的控件树，让 PhoneGo 知道每个按钮是什么，而不是盲目点击坐标。", "Reads the view tree of the agent's display so PhoneGo knows what each button is instead of tapping coordinates blindly.");
        put("虚拟屏", "Agent's screen");
        put("双指缩放 · 双击放大 · 拖动平移", "Pinch to zoom · double-tap to enlarge · drag to pan");
        put("展开全部（共 %1$d 项）", "Show all (%1$d)");
        put("%1$d 次调用", "%1$d calls");
        put("虚拟屏 · 第 %1$d 步", "Agent's screen · step %1$d");
        put("花钱时不再问我（自动下单/支付）", "Never ask me about money (auto-pay / auto-order)");
        put("打开后，涉及支付、下单、提交订单的步骤会直接执行，不再询问。", "With this on, paying, ordering and submitting run without asking again.");
    }

    /** Translate every TextView under {@code root} that has a mapping. */
    static void apply(android.view.View root) {
        if (!english() || root == null) {
            return;
        }
        if (root instanceof android.widget.TextView) {
            android.widget.TextView tv = (android.widget.TextView) root;
            CharSequence text = tv.getText();
            if (text != null) {
                String mapped = EN.get(text.toString());
                if (mapped != null) {
                    tv.setText(mapped);
                }
            }
            CharSequence hint = tv.getHint();
            if (hint != null) {
                String mapped = EN.get(hint.toString());
                if (mapped != null) {
                    tv.setHint(mapped);
                }
            }
        }
        if (root instanceof android.view.ViewGroup) {
            android.view.ViewGroup group = (android.view.ViewGroup) root;
            for (int i = 0; i < group.getChildCount(); i++) {
                apply(group.getChildAt(i));
            }
        }
    }

    private static void put(String zh, String en) {
        EN.put(zh, en);
    }
}
