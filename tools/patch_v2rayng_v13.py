#!/usr/bin/env python3
"""Create the WPS VPN Android client from v2rayNG 2.2.6.

Changes:
- application/launcher name: WPS
- clean premium one-screen UI without settings
- no embedded subscription URL
- user adds/replaces/removes their own HTTPS subscription
- server list is visible on the main screen
- user can select a server and test real delay for one or all servers
- WPS-branded foreground VPN notification icon
- automatic Remnawave-compatible HWID and device headers
- legacy embedded Win Phone Store subscription is removed on first launch
- safe layout for display cutouts and system navigation
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

APP_NAME = "WPS"
APPLICATION_ID = "name.winphonestore.wps"
USER_SUBSCRIPTION_ID = "wps-user-subscription"
LEGACY_SUBSCRIPTION_ID = "win-phone-store-embedded"
PATCH_VERSION = "v13-wps-user-agent-subscription-refresh"


def require(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Required upstream file not found: {path}")
    return path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Could not patch {label}; expected one exact match, got {count}")
    return text.replace(old, new, 1)


def ensure_import(source: str, import_line: str) -> str:
    if import_line in source:
        return source
    match = re.search(r"^package\s+[A-Za-z0-9_.]+\s*$", source, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not add import: {import_line}")
    return source[: match.end()] + "\n\n" + import_line + source[match.end() :]


def patch_gradle(app_dir: Path) -> None:
    path = require(app_dir / "build.gradle.kts")
    text = path.read_text(encoding="utf-8")

    updated, count = re.subn(
        r'applicationId\s*=\s*"com\.v2ray\.ang"',
        f'applicationId = "{APPLICATION_ID}"',
        text,
        count=1,
    )
    if count != 1 and APPLICATION_ID not in text:
        raise RuntimeError("Could not patch applicationId")
    text = updated if count == 1 else text

    # Keep one stable package ID for every flavor.
    text = re.sub(
        r'^\s*applicationIdSuffix\s*=\s*"\.fdroid"\s*\n',
        "",
        text,
        flags=re.MULTILINE,
    )
    text = text.replace('buildConfigField("String", "DISTRIBUTION", "\\"F-Droid\\"")',
                        'buildConfigField("String", "DISTRIBUTION", "\\"WPS\\"")')
    text = text.replace('buildConfigField("String", "DISTRIBUTION", "\\"Play Store\\"")',
                        'buildConfigField("String", "DISTRIBUTION", "\\"WPS\\"")')
    text = text.replace('output.outputFileName = "v2rayNG_', 'output.outputFileName = "WPS_')
    path.write_text(text, encoding="utf-8")


def patch_app_name(app_dir: Path) -> None:
    changed = 0
    for path in app_dir.rglob("strings.xml"):
        if "/res/values" not in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r'(<string\s+name="app_name"[^>]*>).*?(</string>)',
            rf'\1{APP_NAME}\2',
            text,
            flags=re.DOTALL,
        )
        if count:
            path.write_text(updated, encoding="utf-8")
            changed += count

    # Explicit flavor overlays prevent the launcher from showing "v2rayNG (F-Droid)".
    for flavor in ("fdroid", "playstore"):
        path = app_dir / f"src/{flavor}/res/values/wps_app_name.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<resources><string name="app_name" translatable="false">WPS</string></resources>\n',
            encoding="utf-8",
        )
    if changed == 0:
        raise RuntimeError("No app_name resource was found")


def patch_manifest(app_dir: Path) -> None:
    path = require(app_dir / "src/main/AndroidManifest.xml")
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'(<application\b[^>]*?\bandroid:label=)"[^"]*"',
        rf'\1"{APP_NAME}"',
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count == 0:
        updated, count = re.subn(
            r'<application\b',
            f'<application android:label="{APP_NAME}"',
            text,
            count=1,
        )
    if count != 1:
        raise RuntimeError("Could not set Android application label")
    path.write_text(updated, encoding="utf-8")



def patch_http_util(app_dir: Path) -> None:
    """Send a stable app-generated HWID and device metadata on subscription requests."""
    path = require(app_dir / "src/main/java/com/v2ray/ang/util/HttpUtil.kt")
    source = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in source else "\n"

    upstream_user_agent = "\"v2rayNG/${BuildConfig.VERSION_NAME}\""
    wps_user_agent = "\"WPS/${BuildConfig.VERSION_NAME}\""
    if wps_user_agent not in source:
        if upstream_user_agent not in source:
            raise RuntimeError("Could not find the upstream subscription User-Agent")
        source = source.replace(upstream_user_agent, wps_user_agent, 1)

    for import_line in (
        "import android.os.Build",
        "import com.v2ray.ang.handler.MmkvManager",
        "import java.util.UUID",
    ):
        source = ensure_import(source, import_line)

    helper_members = """    private const val WPS_HWID_KEY = "wps_device_hwid_v1"

    @Volatile
    private var cachedWpsDeviceHwid: String? = null

    /**
     * Stable installation-scoped identifier for Remnawave HWID limits.
     * This is not an IMEI, hardware serial, phone number or advertising ID.
     */
    @Synchronized
    private fun getWpsDeviceHwid(): String {
        cachedWpsDeviceHwid?.let { return it }

        val validPattern = Regex("^[A-Za-z0-9=-]{10,64}$")
        val stored = MmkvManager.decodeSettingsString(WPS_HWID_KEY)
            ?.trim()
            ?.takeIf { validPattern.matches(it) }

        val hwid = stored ?: UUID.randomUUID().toString().also {
            MmkvManager.encodeSettings(WPS_HWID_KEY, it)
        }

        cachedWpsDeviceHwid = hwid
        return hwid
    }

    private fun getWpsDeviceModel(): String {
        val manufacturer = Build.MANUFACTURER.trim()
        val model = Build.MODEL.trim()

        return when {
            model.isBlank() && manufacturer.isBlank() -> "Android device"
            manufacturer.isBlank() -> model
            model.isBlank() -> manufacturer
            model.startsWith(manufacturer, ignoreCase = true) -> model
            else -> "$manufacturer $model"
        }
    }

    private fun applyWpsDeviceHeaders(requestBuilder: Request.Builder) {
        requestBuilder
            .header("x-hwid", getWpsDeviceHwid())
            .header("x-device-os", "Android")
            .header("x-ver-os", Build.VERSION.RELEASE.ifBlank { Build.VERSION.SDK_INT.toString() })
            .header("x-device-model", getWpsDeviceModel())
    }

    private fun validateWpsHwidResponse(response: okhttp3.Response) {
        fun headerIsTrue(name: String): Boolean {
            return response.header(name)?.equals("true", ignoreCase = true) == true
        }

        if (headerIsTrue("x-hwid-max-devices-reached") || headerIsTrue("x-hwid-limit")) {
            throw IOException("Достигнут лимит устройств для этой подписки")
        }

        if (headerIsTrue("x-hwid-not-supported")) {
            throw IOException("Панель Remnawave не получила HWID устройства")
        }
    }

"""

    if "private const val WPS_HWID_KEY" not in source:
        lines = source.splitlines(keepends=True)
        object_index = next(
            (i for i, line in enumerate(lines) if line.strip() == "object HttpUtil {"),
            None,
        )
        if object_index is None:
            raise RuntimeError("Could not find object HttpUtil")
        helper = helper_members.replace("\n", newline)
        lines.insert(object_index + 1, newline + helper)
        source = "".join(lines)

    function_start = source.find("fun getUrlContentWithUserAgent")
    function_end = source.find("private fun applyEmbeddedBasicAuthHeader", function_start)
    if function_start < 0 or function_end < 0:
        raise RuntimeError("Could not locate getUrlContentWithUserAgent function")

    function_text = source[function_start:function_end]
    function_lines = function_text.splitlines(keepends=True)

    if "applyWpsDeviceHeaders(requestBuilder)" not in function_text:
        auth_index = next(
            (
                i for i, line in enumerate(function_lines)
                if "applyEmbeddedBasicAuthHeader(currentUrl, requestBuilder)" in line
            ),
            None,
        )
        if auth_index is None:
            raise RuntimeError("Could not find subscription request builder insertion point")
        auth_line = function_lines[auth_index]
        indent = auth_line[: len(auth_line) - len(auth_line.lstrip())]
        function_lines.insert(
            auth_index,
            f"{indent}applyWpsDeviceHeaders(requestBuilder){newline}",
        )

    rebuilt_function = "".join(function_lines)

    if "validateWpsHwidResponse(response)" not in rebuilt_function:
        response_lines = rebuilt_function.splitlines(keepends=True)
        call_index = next(
            (
                i for i, line in enumerate(response_lines)
                if "client.newCall(requestBuilder.build()).execute().use { response ->" in line
            ),
            None,
        )
        if call_index is None:
            raise RuntimeError("Could not find subscription response insertion point")
        call_line = response_lines[call_index]
        indent = call_line[: len(call_line) - len(call_line.lstrip())] + "    "
        response_lines.insert(
            call_index + 1,
            f"{indent}validateWpsHwidResponse(response){newline}",
        )
        rebuilt_function = "".join(response_lines)

    source = source[:function_start] + rebuilt_function + source[function_end:]
    path.write_text(source, encoding="utf-8")


def patch_notification_manager(app_dir: Path) -> None:
    """Use the WPS logo for the foreground VPN notification."""
    path = require(app_dir / "src/main/java/com/v2ray/ang/handler/NotificationManager.kt")
    source = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in source else "\n"

    source = ensure_import(source, "import android.graphics.BitmapFactory")
    source = ensure_import(source, "import android.graphics.Color")

    small_icon_pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)\.setSmallIcon\([^)\r\n]+\)[ \t]*$"
    )
    match = small_icon_pattern.search(source)
    if not match:
        raise RuntimeError("Could not find NotificationCompat setSmallIcon call")

    indent = match.group("indent")
    branded_lines = (
        f"{indent}.setSmallIcon(R.drawable.wps_ic_notification){newline}"
        f"{indent}.setLargeIcon(BitmapFactory.decodeResource(service.resources, R.mipmap.ic_launcher)){newline}"
        f"{indent}.setColor(Color.parseColor(\"#18DDD0\"))"
    )
    source = source[:match.start()] + branded_lines + source[match.end():]

    source = re.sub(
        r"\.setSmallIcon\(R\.drawable\.[A-Za-z0-9_]+\)",
        ".setSmallIcon(R.drawable.wps_ic_notification)",
        source,
    )

    path.write_text(source, encoding="utf-8")


def patch_main_activity(app_dir: Path) -> None:
    path = require(app_dir / "src/main/java/com/v2ray/ang/ui/MainActivity.kt")
    text = path.read_text(encoding="utf-8")

    imports = [
        "import android.graphics.Color",
        "import androidx.core.view.ViewCompat",
        "import androidx.core.view.WindowCompat",
        "import androidx.core.view.WindowInsetsCompat",
        "import androidx.core.view.WindowInsetsControllerCompat",
        "import androidx.recyclerview.widget.LinearLayoutManager",
        "import com.google.android.material.dialog.MaterialAlertDialogBuilder",
        "import com.google.android.material.textfield.TextInputEditText",
        "import com.google.android.material.textfield.TextInputLayout",
        "import com.v2ray.ang.dto.TestServiceMessage",
        "import com.v2ray.ang.dto.entities.SubscriptionCache",
        "import com.v2ray.ang.dto.entities.SubscriptionItem",
        "import com.v2ray.ang.util.MessageUtil",
    ]
    for line in imports:
        text = ensure_import(text, line)

    text = replace_once(
        text,
        "    private lateinit var groupPagerAdapter: GroupPagerAdapter\n    private var tabMediator: TabLayoutMediator? = null",
        "    private lateinit var groupPagerAdapter: GroupPagerAdapter\n    private lateinit var wpsServerAdapter: WpsServerAdapter\n    private var tabMediator: TabLayoutMediator? = null",
        "WPS server adapter property",
    )

    text = replace_once(
        text,
        "        setContentView(binding.root)\n        setupToolbar(binding.toolbar, false, getString(R.string.title_server))",
        "        setContentView(binding.root)\n        configureWpsWindow()\n        clearLegacyEmbeddedSubscription()\n        setupToolbar(binding.toolbar, false, getString(R.string.title_server))\n        supportActionBar?.hide()\n        hideUpstreamChrome()",
        "legacy subscription cleanup",
    )

    text = replace_once(
        text,
        "        binding.fab.setOnClickListener { handleFabAction() }\n        binding.layoutTest.setOnClickListener { handleLayoutTestClick() }",
        """        binding.fab.setOnClickListener { handleFabAction() }
        binding.layoutTest.setOnClickListener { handleLayoutTestClick() }
        binding.btnAddKey.setOnClickListener { showAddKeyDialog() }
        binding.btnRemoveKey.setOnClickListener { removeUserSubscription() }
        binding.btnRefreshSubscription.setOnClickListener { refreshUserSubscription() }
        binding.btnPingAll.setOnClickListener { pingAllWpsServers() }
        binding.navHome.setOnClickListener { }
        binding.navLogs.setOnClickListener {
            startActivity(Intent(this, LogcatActivity::class.java))
        }
        setupWpsServerList()
        mainViewModel.updateListAction.observe(this) {
            refreshWpsServerList()
        }""",
        "WPS UI listeners",
    )

    text = replace_once(
        text,
        "        binding.tabGroup.isVisible = groups.size > 1",
        "        binding.tabGroup.isVisible = false",
        "hide upstream subscription tabs",
    )

    text = replace_once(
        text,
        "        SubscriptionUpdater.sync()\n        mainViewModel.reloadServerList()",
        """        SubscriptionUpdater.sync()
        mainViewModel.reloadServerList()
        lifecycleScope.launch {
            delay(300)
            hideUpstreamChrome()
            refreshWpsState()
            refreshWpsServerList()
        }""",
        "initial WPS UI refresh",
    )

    text = replace_once(
        text,
        "    private fun handleFabAction() {\n        applyRunningState(isLoading = true, isRunning = false)",
        """    private fun handleFabAction() {
        if (mainViewModel.isRunning.value != true && MmkvManager.getSelectServer().isNullOrEmpty()) {
            showAddKeyDialog()
            return
        }
        applyRunningState(isLoading = true, isRunning = false)""",
        "connect guard",
    )

    # MaterialButton uses setIconResource instead of ImageView's setImageResource.
    text = text.replace("binding.fab.setImageResource(", "binding.fab.setIconResource(")

    text = replace_once(
        text,
        "        if (isLoading) {\n            binding.fab.setIconResource(R.drawable.ic_fab_check)\n            return\n        }",
        """        if (isLoading) {
            binding.fab.setIconResource(R.drawable.ic_fab_check)
            binding.fab.text = "ПОДОЖДИТЕ"
            binding.fab.isEnabled = false
            return
        }""",
        "WPS loading button state",
    )

    text = replace_once(
        text,
        "            binding.fab.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(this, R.color.color_fab_active))\n            binding.fab.contentDescription = getString(R.string.action_stop_service)\n            setTestState(getString(R.string.connection_connected))",
        """            binding.fab.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#FF7A12"))
            binding.fab.setTextColor(Color.parseColor("#071012"))
            binding.fab.iconTint = ColorStateList.valueOf(Color.parseColor("#071012"))
            binding.fab.strokeWidth = 0
            binding.fab.text = "ОТКЛЮЧИТЬ"
            binding.fab.isEnabled = true
            binding.fab.alpha = 1.0f
            binding.fab.contentDescription = getString(R.string.action_stop_service)
            binding.tvButtonLabel.isVisible = false
            binding.tvStatusTitle.text = "VPN подключён"
            setTestState("Соединение защищено")""",
        "connected WPS state",
    )

    text = replace_once(
        text,
        "            binding.fab.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(this, R.color.color_fab_inactive))\n            binding.fab.contentDescription = getString(R.string.tasker_start_service)\n            setTestState(getString(R.string.connection_not_connected))",
        """            binding.fab.contentDescription = getString(R.string.tasker_start_service)
            binding.tvButtonLabel.isVisible = false
            refreshWpsState()""",
        "disconnected WPS state",
    )

    marker = "    private fun setupNavigationDrawer() {"
    if marker not in text:
        raise RuntimeError("Could not find setupNavigationDrawer() marker")

    methods = '''    private fun configureWpsWindow() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.parseColor("#020506")
        WindowInsetsControllerCompat(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }
        ViewCompat.setOnApplyWindowInsetsListener(binding.wpsSafeArea) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val displayCutout = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            val topInset = maxOf(systemBars.top, displayCutout.top)
            val bottomInset = maxOf(systemBars.bottom, displayCutout.bottom)
            val extraTop = (10f * resources.displayMetrics.density).toInt()
            view.setPadding(0, topInset + extraTop, 0, bottomInset)
            insets
        }
        ViewCompat.requestApplyInsets(binding.wpsSafeArea)
    }

    private fun hideUpstreamChrome() {
        binding.toolbar.isVisible = false
        binding.tabGroup.isVisible = false
        binding.viewPager.isVisible = false
        binding.navView.isVisible = false
    }

    private fun clearLegacyEmbeddedSubscription() {
        if (MmkvManager.decodeSubscription(WPS_LEGACY_SUBSCRIPTION_ID) != null) {
            MmkvManager.removeSubscription(WPS_LEGACY_SUBSCRIPTION_ID)
        }
    }

    private fun refreshWpsState() {
        val item = MmkvManager.decodeSubscription(WPS_USER_SUBSCRIPTION_ID)
        val servers = MmkvManager.decodeServerList(WPS_USER_SUBSCRIPTION_ID)
        val hasKey = !item?.url.isNullOrBlank()
        val isRunning = mainViewModel.isRunning.value == true
        val canConnect = isRunning || servers.isNotEmpty()

        hideUpstreamChrome()
        binding.btnAddKey.text = if (hasKey) "ИЗМЕНИТЬ КЛЮЧ" else "ДОБАВИТЬ КЛЮЧ"
        binding.btnRemoveKey.isVisible = hasKey
        binding.keyActionSpacer.isVisible = hasKey
        binding.btnRefreshSubscription.isVisible = hasKey
        binding.btnAddKey.isEnabled = !isRunning
        binding.btnRemoveKey.isEnabled = !isRunning
        binding.btnRefreshSubscription.isEnabled = hasKey

        if (hasKey) {
            binding.btnAddKey.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#0B181A"))
            binding.btnAddKey.setTextColor(Color.parseColor("#19E2D5"))
            binding.btnAddKey.iconTint = ColorStateList.valueOf(Color.parseColor("#19E2D5"))
            binding.btnAddKey.strokeColor = ColorStateList.valueOf(Color.parseColor("#365B5D"))
            binding.btnAddKey.strokeWidth = 2
        } else {
            binding.btnAddKey.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#19E2D5"))
            binding.btnAddKey.setTextColor(Color.parseColor("#001716"))
            binding.btnAddKey.iconTint = ColorStateList.valueOf(Color.parseColor("#001716"))
            binding.btnAddKey.strokeWidth = 0
        }

        if (!isRunning) {
            binding.fab.text = "ПОДКЛЮЧИТЬ"
            binding.fab.setIconResource(R.drawable.ic_play_24dp)
            binding.fab.isEnabled = canConnect
            binding.fab.alpha = if (canConnect) 1.0f else 0.72f
            if (canConnect) {
                binding.fab.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#19E2D5"))
                binding.fab.setTextColor(Color.parseColor("#001716"))
                binding.fab.iconTint = ColorStateList.valueOf(Color.parseColor("#001716"))
                binding.fab.strokeWidth = 0
            } else {
                binding.fab.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#10191B"))
                binding.fab.setTextColor(Color.parseColor("#536367"))
                binding.fab.iconTint = ColorStateList.valueOf(Color.parseColor("#536367"))
                binding.fab.strokeColor = ColorStateList.valueOf(Color.parseColor("#243436"))
                binding.fab.strokeWidth = 2
            }
            when {
                !hasKey -> {
                    binding.tvStatusTitle.text = "Подписка не добавлена"
                    binding.tvTestState.text = "Добавьте свою подписку для подключения к VPN-сети"
                    binding.tvSubscriptionMeta.text = "Клиент добавляет свой ключ самостоятельно"
                }
                servers.isEmpty() -> {
                    binding.tvStatusTitle.text = "Серверы не найдены"
                    binding.tvTestState.text = "Проверьте ссылку подписки и попробуйте снова"
                    binding.tvSubscriptionMeta.text = "Ключ сохранён, но конфигурация не загружена"
                }
                else -> {
                    val selectedName = MmkvManager.getSelectServer()
                        ?.let { MmkvManager.decodeServerConfig(it)?.remarks }
                        .orEmpty()
                    binding.tvStatusTitle.text = "Готово к подключению"
                    binding.tvTestState.text = if (selectedName.isBlank()) {
                        "Выберите сервер из списка"
                    } else {
                        "Выбран сервер: $selectedName"
                    }
                    binding.tvSubscriptionMeta.text = "Серверов в подписке: ${servers.size}"
                }
            }
        }
        refreshWpsServerList()
    }

    private fun setupWpsServerList() {
        wpsServerAdapter = WpsServerAdapter(
            onSelect = { guid -> selectWpsServer(guid) },
            onPing = { guid -> pingSingleWpsServer(guid) }
        )
        binding.recyclerServers.apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = wpsServerAdapter
            setHasFixedSize(false)
            isNestedScrollingEnabled = false
        }
        refreshWpsServerList()
    }

    private fun refreshWpsServerList() {
        if (!::wpsServerAdapter.isInitialized) return

        val guids = MmkvManager.decodeServerList(WPS_USER_SUBSCRIPTION_ID)
        val rows = guids.mapNotNull { guid ->
            val profile = MmkvManager.decodeServerConfig(guid) ?: return@mapNotNull null
            val affiliation = MmkvManager.decodeServerAffiliationInfo(guid)
            val protocol = profile.configType.name
            val network = profile.network?.takeIf { it.isNotBlank() }?.uppercase()
            val details = listOfNotNull(
                protocol,
                network,
                profile.server?.takeIf { it.isNotBlank() }
            ).joinToString(" • ")
            WpsServerRow(
                guid = guid,
                name = profile.remarks.ifBlank { profile.server.orEmpty().ifBlank { "Сервер" } },
                details = details,
                delayMillis = affiliation?.testDelayMillis ?: 0L
            )
        }

        val selectedGuid = MmkvManager.getSelectServer()
        binding.serverSection.isVisible = rows.isNotEmpty()
        binding.tvServerCount.text = "${rows.size} серверов"
        wpsServerAdapter.submit(rows, selectedGuid)

        val allFinished = rows.isNotEmpty() && rows.all { it.delayMillis != 0L }
        if (allFinished) {
            binding.btnPingAll.isEnabled = true
            binding.btnPingAll.text = "ПРОВЕРИТЬ ВСЕ"
            binding.tvPingSummary.text = "Проверка завершена"
            wpsServerAdapter.clearPinging()
        }
    }

    private fun selectWpsServer(guid: String) {
        if (guid == MmkvManager.getSelectServer()) return
        MmkvManager.setSelectServer(guid)
        refreshWpsServerList()
        refreshWpsState()
        if (mainViewModel.isRunning.value == true) {
            restartV2Ray()
        }
    }

    private fun pingAllWpsServers() {
        val guids = MmkvManager.decodeServerList(WPS_USER_SUBSCRIPTION_ID)
        if (guids.isEmpty()) {
            toast("Сначала добавьте подписку")
            return
        }

        MmkvManager.clearAllTestDelayResults(guids)
        wpsServerAdapter.markAllPinging(guids)
        binding.btnPingAll.isEnabled = false
        binding.btnPingAll.text = "ПРОВЕРКА…"
        binding.tvPingSummary.text = "Проверяем задержку всех серверов"

        MessageUtil.sendMsg2TestService(
            this,
            TestServiceMessage(key = AppConfig.MSG_MEASURE_CONFIG_CANCEL)
        )
        MessageUtil.sendMsg2TestService(
            this,
            TestServiceMessage(
                key = AppConfig.MSG_MEASURE_CONFIG_START,
                subscriptionId = WPS_USER_SUBSCRIPTION_ID,
                serverGuids = guids
            )
        )

        lifecycleScope.launch {
            delay(45000)
            binding.btnPingAll.isEnabled = true
            binding.btnPingAll.text = "ПРОВЕРИТЬ ВСЕ"
            binding.tvPingSummary.text = "Нажмите ПИНГ для отдельного сервера"
            wpsServerAdapter.clearPinging()
            refreshWpsServerList()
        }
    }

    private fun pingSingleWpsServer(guid: String) {
        MmkvManager.clearAllTestDelayResults(listOf(guid))
        wpsServerAdapter.markPinging(guid)
        binding.tvPingSummary.text = "Проверяем выбранный сервер"

        MessageUtil.sendMsg2TestService(
            this,
            TestServiceMessage(key = AppConfig.MSG_MEASURE_CONFIG_CANCEL)
        )
        MessageUtil.sendMsg2TestService(
            this,
            TestServiceMessage(
                key = AppConfig.MSG_MEASURE_CONFIG_START,
                subscriptionId = WPS_USER_SUBSCRIPTION_ID,
                serverGuids = listOf(guid)
            )
        )

        lifecycleScope.launch {
            delay(15000)
            wpsServerAdapter.clearPinging(guid)
            binding.tvPingSummary.text = "Нажмите ПИНГ для отдельного сервера"
            refreshWpsServerList()
        }
    }

    private fun showAddKeyDialog() {
        if (mainViewModel.isRunning.value == true) {
            toast("Сначала отключите VPN")
            return
        }

        val content = layoutInflater.inflate(R.layout.dialog_add_key, null)
        val inputLayout = content.findViewById<TextInputLayout>(R.id.inputLayoutKey)
        val input = content.findViewById<TextInputEditText>(R.id.inputKey)
        val currentUrl = MmkvManager.decodeSubscription(WPS_USER_SUBSCRIPTION_ID)?.url.orEmpty()
        input.setText(currentUrl)
        input.setSelection(input.text?.length ?: 0)

        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(if (currentUrl.isBlank()) "Добавить подписку" else "Изменить подписку")
            .setMessage("Вставьте персональную HTTPS-ссылку, полученную у вашего VPN-провайдера.")
            .setView(content)
            .setNegativeButton("ОТМЕНА", null)
            .setPositiveButton("СОХРАНИТЬ", null)
            .create()

        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val url = input.text?.toString()?.trim().orEmpty()
                when {
                    url.isBlank() -> inputLayout.error = "Вставьте ссылку подписки"
                    !url.startsWith("https://", ignoreCase = true) ->
                        inputLayout.error = "Ссылка должна начинаться с https://"
                    else -> {
                        inputLayout.error = null
                        dialog.dismiss()
                        saveUserSubscription(url)
                    }
                }
            }
        }
        dialog.show()
    }

    private fun saveUserSubscription(url: String) {
        binding.btnAddKey.isEnabled = false
        binding.btnRemoveKey.isEnabled = false
        binding.fab.isEnabled = false
        binding.tvStatusTitle.text = "Добавление подписки"
        setTestState("Получение конфигурации…")
        showLoading()

        lifecycleScope.launch(Dispatchers.IO) {
            var failure: Throwable? = null
            val servers = try {
                MmkvManager.removeSubscription(WPS_USER_SUBSCRIPTION_ID)
                val subItem = SubscriptionItem().apply {
                    remarks = "WPS"
                    this.url = url
                    enabled = true
                }
                MmkvManager.encodeSubscription(WPS_USER_SUBSCRIPTION_ID, subItem)
                AngConfigManager.updateConfigViaSub(
                    SubscriptionCache(WPS_USER_SUBSCRIPTION_ID, subItem)
                )
                MmkvManager.decodeServerList(WPS_USER_SUBSCRIPTION_ID).also { list ->
                    list.firstOrNull()?.let { MmkvManager.setSelectServer(it) }
                }
            } catch (e: Throwable) {
                failure = e
                emptyList()
            }

            withContext(Dispatchers.Main) {
                hideLoading()
                mainViewModel.subscriptionIdChanged(WPS_USER_SUBSCRIPTION_ID)
                setupGroupTab()
                hideUpstreamChrome()
                mainViewModel.reloadServerList()
                refreshGroupTabTitles(true)
                refreshWpsState()
                when {
                    failure != null -> toast(failure?.message ?: "Не удалось добавить подписку")
                    servers.isEmpty() -> toast("Подписка сохранена, но серверы не найдены")
                    else -> toast("Подписка добавлена. Серверов: ${servers.size}")
                }
            }
        }
    }

    private fun refreshUserSubscription() {
        val subItem = MmkvManager.decodeSubscription(WPS_USER_SUBSCRIPTION_ID)
        if (subItem?.url.isNullOrBlank()) {
            toast("Сначала добавьте подписку")
            return
        }

        val wasRunning = mainViewModel.isRunning.value == true
        binding.btnRefreshSubscription.isEnabled = false
        binding.btnRefreshSubscription.text = "ОБНОВЛЕНИЕ…"
        binding.btnAddKey.isEnabled = false
        binding.btnRemoveKey.isEnabled = false
        binding.fab.isEnabled = false
        binding.tvStatusTitle.text = "Обновление подписки"
        binding.tvTestState.text = "Загружаем новые конфигурации…"
        showLoading()

        lifecycleScope.launch(Dispatchers.IO) {
            var failure: Throwable? = null
            val result = try {
                AngConfigManager.updateConfigViaSub(
                    SubscriptionCache(WPS_USER_SUBSCRIPTION_ID, subItem!!)
                )
            } catch (e: Throwable) {
                failure = e
                null
            }

            val servers = MmkvManager.decodeServerList(WPS_USER_SUBSCRIPTION_ID)
            val selectedGuid = MmkvManager.getSelectServer()
            if (servers.isNotEmpty() && (selectedGuid.isNullOrBlank() || selectedGuid !in servers)) {
                MmkvManager.setSelectServer(servers.first())
            }

            withContext(Dispatchers.Main) {
                hideLoading()
                binding.btnRefreshSubscription.text = "ОБНОВИТЬ ПОДПИСКУ"
                mainViewModel.subscriptionIdChanged(WPS_USER_SUBSCRIPTION_ID)
                setupGroupTab()
                hideUpstreamChrome()
                mainViewModel.reloadServerList()
                refreshGroupTabTitles(true)
                refreshWpsState()

                when {
                    failure != null -> {
                        toast(failure?.message ?: "Не удалось обновить подписку")
                    }
                    result == null || result.successCount <= 0 -> {
                        toast("Не удалось обновить подписку")
                    }
                    else -> {
                        toast("Подписка обновлена. Конфигураций: ${result.configCount}")
                        if (wasRunning) {
                            restartV2Ray()
                        }
                    }
                }
            }
        }
    }

    private fun removeUserSubscription() {
        if (mainViewModel.isRunning.value == true) {
            toast("Сначала отключите VPN")
            return
        }
        MaterialAlertDialogBuilder(this)
            .setTitle("Удалить подписку?")
            .setMessage("Ключ и загруженные серверы будут удалены с этого телефона.")
            .setNegativeButton("ОТМЕНА", null)
            .setPositiveButton("УДАЛИТЬ") { _, _ ->
                MmkvManager.removeSubscription(WPS_USER_SUBSCRIPTION_ID)
                mainViewModel.subscriptionIdChanged("")
                setupGroupTab()
                hideUpstreamChrome()
                mainViewModel.reloadServerList()
                refreshGroupTabTitles(true)
                refreshWpsState()
                toast("Подписка удалена")
            }
            .show()
    }

'''
    text = text.replace(marker, methods + marker, 1)
    path.write_text(text, encoding="utf-8")
    print(f"Patched MainActivity with user subscription UI: {path}")


def write_server_adapter(app_dir: Path) -> None:
    path = app_dir / "src/main/java/com/v2ray/ang/ui/WpsServerAdapter.kt"
    path.write_text(r"""package com.v2ray.ang.ui

import android.graphics.Color
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.core.view.isVisible
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.v2ray.ang.R

data class WpsServerRow(
    val guid: String,
    val name: String,
    val details: String,
    val delayMillis: Long
)

class WpsServerAdapter(
    private val onSelect: (String) -> Unit,
    private val onPing: (String) -> Unit
) : RecyclerView.Adapter<WpsServerAdapter.ServerViewHolder>() {

    private val items = mutableListOf<WpsServerRow>()
    private val pingingGuids = mutableSetOf<String>()
    private var selectedGuid: String? = null

    fun submit(newItems: List<WpsServerRow>, newSelectedGuid: String?) {
        items.clear()
        items.addAll(newItems)
        selectedGuid = newSelectedGuid
        pingingGuids.removeAll { guid ->
            newItems.firstOrNull { it.guid == guid }?.delayMillis != 0L
        }
        notifyDataSetChanged()
    }

    fun markPinging(guid: String) {
        pingingGuids.add(guid)
        notifyGuid(guid)
    }

    fun markAllPinging(guids: List<String>) {
        pingingGuids.clear()
        pingingGuids.addAll(guids)
        notifyDataSetChanged()
    }

    fun clearPinging(guid: String? = null) {
        if (guid == null) {
            pingingGuids.clear()
            notifyDataSetChanged()
        } else if (pingingGuids.remove(guid)) {
            notifyGuid(guid)
        }
    }

    private fun notifyGuid(guid: String) {
        val index = items.indexOfFirst { it.guid == guid }
        if (index >= 0) notifyItemChanged(index)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ServerViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_wps_server, parent, false)
        return ServerViewHolder(view)
    }

    override fun onBindViewHolder(holder: ServerViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class ServerViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val card = itemView.findViewById<MaterialCardView>(R.id.serverCard)
        private val name = itemView.findViewById<TextView>(R.id.tvServerName)
        private val details = itemView.findViewById<TextView>(R.id.tvServerDetails)
        private val ping = itemView.findViewById<MaterialButton>(R.id.btnServerPing)
        private val selected = itemView.findViewById<ImageView>(R.id.ivServerSelected)

        fun bind(item: WpsServerRow) {
            val isSelected = item.guid == selectedGuid
            name.text = item.name
            details.text = item.details
            selected.isVisible = isSelected
            card.strokeColor = Color.parseColor(if (isSelected) "#19E2D5" else "#253A3D")
            card.strokeWidth = if (isSelected) dp(2) else dp(1)

            val isPinging = item.guid in pingingGuids && item.delayMillis == 0L
            when {
                isPinging -> {
                    ping.text = "..."
                    ping.setTextColor(Color.parseColor("#93A3A7"))
                    ping.iconTint = android.content.res.ColorStateList.valueOf(Color.parseColor("#93A3A7"))
                }
                item.delayMillis > 0L -> {
                    ping.text = "${item.delayMillis} ms"
                    val color = when {
                        item.delayMillis < 150L -> "#36D67E"
                        item.delayMillis < 300L -> "#FFB020"
                        else -> "#FF5A5F"
                    }
                    ping.setTextColor(Color.parseColor(color))
                    ping.iconTint = android.content.res.ColorStateList.valueOf(Color.parseColor(color))
                }
                item.delayMillis < 0L -> {
                    ping.text = "НЕТ"
                    ping.setTextColor(Color.parseColor("#FF5A5F"))
                    ping.iconTint = android.content.res.ColorStateList.valueOf(Color.parseColor("#FF5A5F"))
                }
                else -> {
                    ping.text = "ПИНГ"
                    ping.setTextColor(Color.parseColor("#19E2D5"))
                    ping.iconTint = android.content.res.ColorStateList.valueOf(Color.parseColor("#19E2D5"))
                }
            }

            card.setOnClickListener { onSelect(item.guid) }
            ping.setOnClickListener {
                onSelect(item.guid)
                onPing(item.guid)
            }
        }

        private fun dp(value: Int): Int =
            (value * itemView.resources.displayMetrics.density).toInt()
    }
}
""", encoding="utf-8")


def write_constants(app_dir: Path) -> None:
    path = app_dir / "src/main/java/com/v2ray/ang/ui/WpsConfig.kt"
    path.write_text(
        f'''package com.v2ray.ang.ui

internal const val WPS_USER_SUBSCRIPTION_ID = "{USER_SUBSCRIPTION_ID}"
internal const val WPS_LEGACY_SUBSCRIPTION_ID = "{LEGACY_SUBSCRIPTION_ID}"
''',
        encoding="utf-8",
    )


def write_layouts(app_dir: Path) -> None:
    layout = app_dir / "src/main/res/layout/activity_main.xml"
    layout.parent.mkdir(parents=True, exist_ok=True)
    layout.write_text(r'''<?xml version="1.0" encoding="utf-8"?>
<androidx.drawerlayout.widget.DrawerLayout
    xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:app="http://schemas.android.com/apk/res-auto"
    android:id="@+id/drawerLayout"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    android:background="@drawable/wps_background">

    <FrameLayout
        android:id="@+id/wpsSafeArea"
        android:layout_width="match_parent"
        android:layout_height="match_parent"
        android:clipToPadding="false">

        <androidx.core.widget.NestedScrollView
            android:layout_width="match_parent"
            android:layout_height="match_parent"
            android:layout_marginBottom="64dp"
            android:clipToPadding="false"
            android:fillViewport="true"
            android:overScrollMode="never">

            <LinearLayout
                android:layout_width="match_parent"
                android:layout_height="wrap_content"
                android:gravity="center_horizontal"
                android:orientation="vertical"
                android:paddingStart="24dp"
                android:paddingTop="2dp"
                android:paddingEnd="24dp"
                android:paddingBottom="28dp">

                <LinearLayout
                    android:layout_width="match_parent"
                    android:layout_height="76dp"
                    android:gravity="center"
                    android:orientation="vertical">

                    <TextView
                        android:layout_width="wrap_content"
                        android:layout_height="wrap_content"
                        android:fontFamily="sans-serif-black"
                        android:includeFontPadding="true"
                        android:letterSpacing="0.07"
                        android:text="WPS"
                        android:textColor="#FFFFFF"
                        android:textSize="30sp" />

                    <TextView
                        android:layout_width="wrap_content"
                        android:layout_height="wrap_content"
                        android:layout_marginTop="-4dp"
                        android:fontFamily="sans-serif-medium"
                        android:includeFontPadding="true"
                        android:letterSpacing="0.34"
                        android:text="VPN"
                        android:textColor="#19E2D5"
                        android:textSize="12sp" />
                </LinearLayout>

                <FrameLayout
                    android:layout_width="176dp"
                    android:layout_height="164dp"
                    android:layout_marginTop="0dp">

                    <View
                        android:layout_width="176dp"
                        android:layout_height="164dp"
                        android:background="@drawable/wps_logo_glow" />

                    <View
                        android:layout_width="126dp"
                        android:layout_height="26dp"
                        android:layout_gravity="bottom|center_horizontal"
                        android:layout_marginBottom="8dp"
                        android:background="@drawable/wps_floor_glow" />

                    <ImageView
                        android:layout_width="136dp"
                        android:layout_height="136dp"
                        android:layout_gravity="center_horizontal|top"
                        android:layout_marginTop="6dp"
                        android:contentDescription="WPS"
                        android:scaleType="fitCenter"
                        android:src="@drawable/wps_logo" />
                </FrameLayout>

                <TextView
                    android:id="@+id/tvStatusTitle"
                    android:layout_width="match_parent"
                    android:layout_height="wrap_content"
                    android:layout_marginTop="4dp"
                    android:fontFamily="sans-serif-medium"
                    android:breakStrategy="balanced"
                    android:gravity="center"
                    android:hyphenationFrequency="none"
                    android:maxLines="2"
                    android:text="Подписка не добавлена"
                    android:textColor="#FFFFFF"
                    android:textSize="21sp" />

                <LinearLayout
                    android:id="@+id/layoutTest"
                    android:layout_width="match_parent"
                    android:layout_height="wrap_content"
                    android:layout_marginTop="8dp"
                    android:gravity="center"
                    android:orientation="vertical">

                    <TextView
                        android:id="@+id/tvTestState"
                        android:layout_width="match_parent"
                        android:layout_height="wrap_content"
                        android:breakStrategy="balanced"
                        android:gravity="center"
                        android:hyphenationFrequency="none"
                        android:lineSpacingExtra="2dp"
                        android:maxLines="3"
                        android:text="Добавьте свою подписку для подключения к VPN-сети"
                        android:textColor="#AAB9BE"
                        android:textSize="14sp" />
                </LinearLayout>

                <LinearLayout
                    android:id="@+id/keyActionsRow"
                    android:layout_width="match_parent"
                    android:layout_height="wrap_content"
                    android:layout_marginTop="22dp"
                    android:gravity="center"
                    android:orientation="horizontal">

                    <com.google.android.material.button.MaterialButton
                        android:id="@+id/btnAddKey"
                        android:layout_width="0dp"
                        android:layout_height="58dp"
                        android:layout_weight="1"
                        android:fontFamily="sans-serif-medium"
                        android:gravity="center"
                        android:maxLines="2"
                        android:paddingStart="14dp"
                        android:paddingEnd="14dp"
                        android:text="ДОБАВИТЬ КЛЮЧ"
                        android:textColor="#001716"
                        android:textSize="14sp"
                        app:backgroundTint="#19E2D5"
                        app:cornerRadius="18dp"
                        app:icon="@drawable/wps_ic_add"
                        app:iconGravity="textStart"
                        app:iconPadding="9dp"
                        app:iconSize="22dp"
                        app:iconTint="#001716" />

                    <Space
                        android:id="@+id/keyActionSpacer"
                        android:layout_width="12dp"
                        android:layout_height="1dp"
                        android:visibility="gone" />

                    <com.google.android.material.button.MaterialButton
                        android:id="@+id/btnRemoveKey"
                        android:layout_width="0dp"
                        android:layout_height="58dp"
                        android:layout_weight="1"
                        android:fontFamily="sans-serif-medium"
                        android:gravity="center"
                        android:maxLines="2"
                        android:paddingStart="12dp"
                        android:paddingEnd="12dp"
                        android:text="УДАЛИТЬ КЛЮЧ"
                        android:textColor="#93A3A7"
                        android:textSize="13sp"
                        android:visibility="gone"
                        app:backgroundTint="#0B181A"
                        app:cornerRadius="18dp"
                        app:icon="@drawable/wps_ic_delete"
                        app:iconGravity="textStart"
                        app:iconPadding="8dp"
                        app:iconSize="21dp"
                        app:iconTint="#93A3A7"
                        app:strokeColor="#365B5D"
                        app:strokeWidth="1dp" />
                </LinearLayout>

                <com.google.android.material.button.MaterialButton
                    android:id="@+id/btnRefreshSubscription"
                    style="@style/Widget.MaterialComponents.Button.OutlinedButton"
                    android:layout_width="match_parent"
                    android:layout_height="52dp"
                    android:layout_marginTop="12dp"
                    android:fontFamily="sans-serif-medium"
                    android:gravity="center"
                    android:letterSpacing="0.03"
                    android:paddingStart="16dp"
                    android:paddingEnd="16dp"
                    android:text="ОБНОВИТЬ ПОДПИСКУ"
                    android:textColor="#19E2D5"
                    android:textSize="13sp"
                    android:visibility="gone"
                    app:cornerRadius="16dp"
                    app:icon="@drawable/wps_ic_refresh"
                    app:iconGravity="textStart"
                    app:iconPadding="9dp"
                    app:iconSize="21dp"
                    app:iconTint="#19E2D5"
                    app:strokeColor="#365B5D"
                    app:strokeWidth="1dp" />

                <TextView
                    android:id="@+id/tvSubscriptionMeta"
                    android:layout_width="match_parent"
                    android:layout_height="wrap_content"
                    android:layout_marginTop="9dp"
                    android:gravity="center"
                    android:maxLines="2"
                    android:text="Клиент добавляет свой ключ самостоятельно"
                    android:textColor="#718086"
                    android:textSize="12sp" />

                <LinearLayout
                    android:layout_width="match_parent"
                    android:layout_height="wrap_content"
                    android:layout_marginTop="8dp"
                    android:gravity="center_vertical"
                    android:orientation="horizontal">

                    <View
                        android:layout_width="0dp"
                        android:layout_height="1dp"
                        android:layout_weight="1"
                        android:background="#263438" />

                    <TextView
                        android:layout_width="wrap_content"
                        android:layout_height="wrap_content"
                        android:paddingStart="14dp"
                        android:paddingEnd="14dp"
                        android:text="ИЛИ"
                        android:textColor="#718086"
                        android:textSize="11sp" />

                    <View
                        android:layout_width="0dp"
                        android:layout_height="1dp"
                        android:layout_weight="1"
                        android:background="#263438" />
                </LinearLayout>

                <com.google.android.material.button.MaterialButton
                    android:id="@+id/fab"
                    android:layout_width="match_parent"
                    android:layout_height="58dp"
                    android:layout_marginTop="18dp"
                    android:contentDescription="Подключить"
                    android:enabled="false"
                    android:fontFamily="sans-serif-medium"
                    android:gravity="center"
                    android:letterSpacing="0.05"
                    android:maxLines="2"
                    android:paddingStart="18dp"
                    android:paddingEnd="18dp"
                    android:text="ПОДКЛЮЧИТЬ"
                    android:textColor="#536367"
                    android:textSize="15sp"
                    app:backgroundTint="#10191B"
                    app:cornerRadius="18dp"
                    app:icon="@drawable/ic_play_24dp"
                    app:iconGravity="textStart"
                    app:iconPadding="12dp"
                    app:iconSize="24dp"
                    app:iconTint="#536367"
                    app:strokeColor="#243436"
                    app:strokeWidth="1dp" />

                <TextView
                    android:id="@+id/tvButtonLabel"
                    android:layout_width="1dp"
                    android:layout_height="1dp"
                    android:visibility="gone" />

                <TextView
                    android:layout_width="wrap_content"
                    android:layout_height="wrap_content"
                    android:layout_marginTop="14dp"
                    android:gravity="center"
                    android:text="Ваш VPN • Ваш ключ • Ваша свобода"
                    android:textColor="#58686D"
                    android:textSize="11sp" />

                <LinearLayout
                    android:id="@+id/serverSection"
                    android:layout_width="match_parent"
                    android:layout_height="wrap_content"
                    android:layout_marginTop="26dp"
                    android:orientation="vertical"
                    android:visibility="gone">

                    <LinearLayout
                        android:layout_width="match_parent"
                        android:layout_height="wrap_content"
                        android:gravity="center_vertical"
                        android:orientation="horizontal">

                        <LinearLayout
                            android:layout_width="0dp"
                            android:layout_height="wrap_content"
                            android:layout_weight="1"
                            android:orientation="vertical">

                            <TextView
                                android:layout_width="wrap_content"
                                android:layout_height="wrap_content"
                                android:fontFamily="sans-serif-medium"
                                android:text="Доступные серверы"
                                android:textColor="#FFFFFF"
                                android:textSize="18sp" />

                            <TextView
                                android:id="@+id/tvServerCount"
                                android:layout_width="wrap_content"
                                android:layout_height="wrap_content"
                                android:layout_marginTop="2dp"
                                android:text="0 серверов"
                                android:textColor="#718086"
                                android:textSize="12sp" />
                        </LinearLayout>

                        <com.google.android.material.button.MaterialButton
                            android:id="@+id/btnPingAll"
                            style="@style/Widget.MaterialComponents.Button.OutlinedButton"
                            android:layout_width="wrap_content"
                            android:layout_height="44dp"
                            android:minWidth="0dp"
                            android:paddingStart="12dp"
                            android:paddingEnd="12dp"
                            android:text="ПРОВЕРИТЬ ВСЕ"
                            android:textColor="#19E2D5"
                            android:textSize="11sp"
                            app:cornerRadius="14dp"
                            app:icon="@drawable/wps_ic_ping"
                            app:iconGravity="textStart"
                            app:iconPadding="6dp"
                            app:iconSize="18dp"
                            app:iconTint="#19E2D5"
                            app:strokeColor="#365B5D"
                            app:strokeWidth="1dp" />
                    </LinearLayout>

                    <TextView
                        android:id="@+id/tvPingSummary"
                        android:layout_width="match_parent"
                        android:layout_height="wrap_content"
                        android:layout_marginTop="8dp"
                        android:text="Нажмите ПИНГ для отдельного сервера"
                        android:textColor="#718086"
                        android:textSize="12sp" />

                    <androidx.recyclerview.widget.RecyclerView
                        android:id="@+id/recyclerServers"
                        android:layout_width="match_parent"
                        android:layout_height="wrap_content"
                        android:layout_marginTop="10dp"
                        android:clipToPadding="false"
                        android:nestedScrollingEnabled="false"
                        android:overScrollMode="never" />
                </LinearLayout>
            </LinearLayout>
        </androidx.core.widget.NestedScrollView>

        <LinearLayout
            android:layout_width="match_parent"
            android:layout_height="64dp"
            android:layout_gravity="bottom"
            android:background="@drawable/wps_bottom_bar"
            android:gravity="center"
            android:orientation="horizontal"
            android:paddingStart="16dp"
            android:paddingEnd="16dp">

            <com.google.android.material.button.MaterialButton
                android:id="@+id/navHome"
                style="@style/Widget.MaterialComponents.Button.TextButton"
                android:layout_width="0dp"
                android:layout_height="match_parent"
                android:layout_weight="1"
                android:gravity="center"
                android:text="Главная"
                android:textColor="#19E2D5"
                android:textSize="11sp"
                app:icon="@drawable/wps_ic_home"
                app:iconGravity="top"
                app:iconPadding="2dp"
                app:iconTint="#19E2D5" />

            <com.google.android.material.button.MaterialButton
                android:id="@+id/navLogs"
                style="@style/Widget.MaterialComponents.Button.TextButton"
                android:layout_width="0dp"
                android:layout_height="match_parent"
                android:layout_weight="1"
                android:gravity="center"
                android:text="Журналы"
                android:textColor="#849499"
                android:textSize="11sp"
                app:icon="@drawable/wps_ic_log"
                app:iconGravity="top"
                app:iconPadding="2dp"
                app:iconTint="#849499" />

        </LinearLayout>

        <com.google.android.material.appbar.MaterialToolbar
            android:id="@+id/toolbar"
            android:layout_width="1dp"
            android:layout_height="1dp"
            android:visibility="gone" />

        <com.google.android.material.tabs.TabLayout
            android:id="@+id/tabGroup"
            android:layout_width="1dp"
            android:layout_height="1dp"
            android:visibility="gone" />

        <androidx.viewpager2.widget.ViewPager2
            android:id="@+id/viewPager"
            android:layout_width="1dp"
            android:layout_height="1dp"
            android:visibility="invisible" />
    </FrameLayout>

    <com.google.android.material.navigation.NavigationView
        android:id="@+id/navView"
        android:layout_width="1dp"
        android:layout_height="1dp"
        android:layout_gravity="start"
        android:visibility="gone" />
</androidx.drawerlayout.widget.DrawerLayout>
''', encoding="utf-8")

    item_server = app_dir / "src/main/res/layout/item_wps_server.xml"
    item_server.write_text(r"""<?xml version="1.0" encoding="utf-8"?>
<com.google.android.material.card.MaterialCardView
    xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:app="http://schemas.android.com/apk/res-auto"
    android:id="@+id/serverCard"
    android:layout_width="match_parent"
    android:layout_height="wrap_content"
    android:layout_marginBottom="9dp"
    android:clickable="true"
    android:focusable="true"
    android:foreground="?attr/selectableItemBackground"
    app:cardBackgroundColor="#0B1517"
    app:cardCornerRadius="16dp"
    app:cardElevation="0dp"
    app:strokeColor="#253A3D"
    app:strokeWidth="1dp">

    <LinearLayout
        android:layout_width="match_parent"
        android:layout_height="76dp"
        android:gravity="center_vertical"
        android:orientation="horizontal"
        android:paddingStart="13dp"
        android:paddingEnd="10dp">

        <FrameLayout
            android:layout_width="40dp"
            android:layout_height="40dp"
            android:background="@drawable/wps_server_icon_bg">

            <ImageView
                android:layout_width="22dp"
                android:layout_height="22dp"
                android:layout_gravity="center"
                android:contentDescription="Сервер"
                android:src="@drawable/wps_ic_server"
                app:tint="#19E2D5" />
        </FrameLayout>

        <LinearLayout
            android:layout_width="0dp"
            android:layout_height="wrap_content"
            android:layout_marginStart="12dp"
            android:layout_weight="1"
            android:orientation="vertical">

            <TextView
                android:id="@+id/tvServerName"
                android:layout_width="match_parent"
                android:layout_height="wrap_content"
                android:ellipsize="end"
                android:fontFamily="sans-serif-medium"
                android:maxLines="1"
                android:textColor="#FFFFFF"
                android:textSize="15sp" />

            <TextView
                android:id="@+id/tvServerDetails"
                android:layout_width="match_parent"
                android:layout_height="wrap_content"
                android:layout_marginTop="4dp"
                android:ellipsize="end"
                android:maxLines="1"
                android:textColor="#718086"
                android:textSize="11sp" />
        </LinearLayout>

        <com.google.android.material.button.MaterialButton
            android:id="@+id/btnServerPing"
            style="@style/Widget.MaterialComponents.Button.TextButton"
            android:layout_width="76dp"
            android:layout_height="44dp"
            android:minWidth="0dp"
            android:paddingStart="6dp"
            android:paddingEnd="6dp"
            android:text="ПИНГ"
            android:textColor="#19E2D5"
            android:textSize="12sp"
            app:cornerRadius="12dp"
            app:icon="@drawable/wps_ic_ping"
            app:iconGravity="textStart"
            app:iconPadding="3dp"
            app:iconSize="16dp"
            app:iconTint="#19E2D5" />

        <ImageView
            android:id="@+id/ivServerSelected"
            android:layout_width="22dp"
            android:layout_height="22dp"
            android:layout_marginStart="2dp"
            android:contentDescription="Выбран"
            android:src="@drawable/wps_ic_selected"
            android:visibility="gone"
            app:tint="#19E2D5" />
    </LinearLayout>
</com.google.android.material.card.MaterialCardView>
""", encoding="utf-8")

    dialog = app_dir / "src/main/res/layout/dialog_add_key.xml"
    dialog.write_text(r'''<?xml version="1.0" encoding="utf-8"?>
<LinearLayout
    xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:app="http://schemas.android.com/apk/res-auto"
    android:layout_width="match_parent"
    android:layout_height="wrap_content"
    android:orientation="vertical"
    android:paddingStart="4dp"
    android:paddingTop="12dp"
    android:paddingEnd="4dp"
    android:paddingBottom="4dp">

    <com.google.android.material.textfield.TextInputLayout
        android:id="@+id/inputLayoutKey"
        style="@style/Widget.MaterialComponents.TextInputLayout.OutlinedBox"
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:hint="Ссылка подписки"
        app:boxStrokeColor="#19E2D5"
        app:endIconMode="clear_text"
        app:helperText="Пример: https://sub.example.com/ваш-ключ">

        <com.google.android.material.textfield.TextInputEditText
            android:id="@+id/inputKey"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:inputType="textUri"
            android:maxLines="3"
            android:minLines="1"
            android:selectAllOnFocus="false"
            android:textSize="14sp" />
    </com.google.android.material.textfield.TextInputLayout>
</LinearLayout>
''', encoding="utf-8")

def write_drawables(app_dir: Path) -> None:
    drawable = app_dir / "src/main/res/drawable"
    drawable.mkdir(parents=True, exist_ok=True)
    files = {
        "wps_background.xml": '''<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <gradient android:angle="315" android:startColor="#010304" android:centerColor="#061214" android:endColor="#020506" />
</shape>\n''',
        "wps_logo_glow.xml": '''<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="oval">
    <gradient android:type="radial" android:gradientRadius="92dp" android:centerColor="#4D19E2D5" android:endColor="#0019E2D5" />
</shape>\n''',
        "wps_floor_glow.xml": '''<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="oval">
    <gradient android:type="radial" android:gradientRadius="68dp" android:centerColor="#8019E2D5" android:endColor="#0019E2D5" />
</shape>\n''',
        "wps_icon_button.xml": '''<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <solid android:color="#0B1416" />
    <corners android:radius="15dp" />
    <stroke android:width="1dp" android:color="#343A3D" />
</shape>\n''',
        "wps_bottom_bar.xml": '''<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <solid android:color="#F2090D0F" />
    <stroke android:width="1dp" android:color="#1E394044" />
</shape>\n''',
        "wps_ic_add.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FF000000" android:pathData="M19,13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></vector>\n''',
        "wps_ic_home.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FFFFFFFF" android:pathData="M12,3 2,12h3v8h6v-5h2v5h6v-8h3L12,3zM17,18h-2v-5H9v5H7v-7.1l5,-4.5 5,4.5V18z"/></vector>\n''',
        "wps_ic_log.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FFFFFFFF" android:pathData="M6,2h9l5,5v15H6V2zM14,4H8v16h10V8h-4V4zM10,11h6v2h-6v-2zM10,15h6v2h-6v-2z"/></vector>\n''',
        "wps_ic_delete.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FFFFFFFF" android:pathData="M6,7h12v14H6V7zM8,9v10h8V9H8zM9,3h6l1,2h4v2H4V5h4l1,-2z"/></vector>\n''',
        "wps_server_icon_bg.xml": '''<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <solid android:color="#102326" />
    <corners android:radius="12dp" />
    <stroke android:width="1dp" android:color="#315154" />
</shape>\n''',
        "wps_ic_server.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FFFFFFFF" android:pathData="M4,3h16v6H4V3zM6,5v2h2V5H6zM4,10h16v6H4v-6zM6,12v2h2v-2H6zM4,17h16v4H4v-4zM6,18v2h2v-2H6z"/></vector>\n''',
        "wps_ic_ping.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FFFFFFFF" android:pathData="M12,21a9,9 0,1 1,9 -9h-2a7,7 0,1 0,-2.05 4.95l1.42,1.42A8.96,8.96 0,0 1,12 21zM13,7v4.59l3.2,3.2 -1.41,1.41L11,12.41V7h2z"/></vector>\n''',
        "wps_ic_refresh.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FFFFFFFF" android:pathData="M17.65,6.35C16.2,4.9 14.21,4 12,4c-4.09,0 -7.19,3.72 -6.39,7.69l-2.08,0C2.81,6.6 6.72,2 12,2c2.76,0 5.26,1.12 7.07,2.93L22,2v8h-8l3.65,-3.65zM6.35,17.65C7.8,19.1 9.79,20 12,20c4.09,0 7.19,-3.72 6.39,-7.69h2.08C21.19,17.4 17.28,22 12,22c-2.76,0 -5.26,-1.12 -7.07,-2.93L2,22v-8h8l-3.65,3.65z"/></vector>\n''',
        "wps_ic_selected.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FFFFFFFF" android:pathData="M9,16.17 4.83,12l-1.42,1.41L9,19 21,7l-1.41,-1.41z"/></vector>\n''',
        "wps_ic_settings.xml": '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24"><path android:fillColor="#FFFFFFFF" android:pathData="M19.4,13a7.9,7.9 0,0 0,0.1 -1,7.9 7.9,0 0,0 -0.1,-1l2.1,-1.7 -2,-3.4 -2.5,1a8,8 0,0 0,-1.7 -1L15,3h-4l-0.4,2.9a8,8 0,0 0,-1.7 1l-2.5,-1 -2,3.4L6.5,11a7.9,7.9 0,0 0,-0.1 1,7.9 7.9,0 0,0 0.1,1l-2.1,1.7 2,3.4 2.5,-1a8,8 0,0 0,1.7 1L11,21h4l0.4,-2.9a8,8 0,0 0,1.7 -1l2.5,1 2,-3.4L19.4,13zM13,16a4,4 0,1 1,0 -8,4 4,0 0,1 0,8zM13,14a2,2 0,1 0,0 -4,2 2,0 0,0 0,4z"/></vector>\n''',
    }
    for name, content in files.items():
        (drawable / name).write_text(content, encoding="utf-8")


def install_resources(app_dir: Path, kit_root: Path) -> None:
    res = app_dir / "src/main/res"
    drawable_nodpi = res / "drawable-nodpi"
    drawable_nodpi.mkdir(parents=True, exist_ok=True)
    shutil.copy2(require(kit_root / "assets/wps_logo.png"), drawable_nodpi / "wps_logo.png")
    shutil.copy2(require(kit_root / "assets/wps_launcher_foreground.png"), drawable_nodpi / "wps_launcher_foreground.png")

    icons_root = require(kit_root / "assets/icons")
    for source_dir in icons_root.glob("mipmap-*"):
        target = res / source_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for icon in source_dir.glob("*.png"):
            shutil.copy2(icon, target / icon.name)

    notification_root = require(kit_root / "assets/notification")
    notification_dirs = list(notification_root.glob("drawable-*"))
    if not notification_dirs:
        raise RuntimeError("WPS notification icon assets were not found")
    for source_dir in notification_dirs:
        target = res / source_dir.name
        target.mkdir(parents=True, exist_ok=True)
        for icon in source_dir.glob("*.png"):
            shutil.copy2(icon, target / icon.name)

    adaptive = res / "mipmap-anydpi-v26"
    adaptive.mkdir(parents=True, exist_ok=True)
    adaptive_xml = '''<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/wps_launcher_background" />
    <foreground android:drawable="@drawable/wps_launcher_foreground" />
</adaptive-icon>\n'''
    (adaptive / "ic_launcher.xml").write_text(adaptive_xml, encoding="utf-8")
    (adaptive / "ic_launcher_round.xml").write_text(adaptive_xml, encoding="utf-8")

    values = res / "values"
    values.mkdir(parents=True, exist_ok=True)
    (values / "wps_colors.xml").write_text('''<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="wps_launcher_background">#050A0B</color>
</resources>\n''', encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--kit-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    root = args.source.resolve()
    kit_root = args.kit_root.resolve()
    app_dir = require(root / "app")

    print(f"WPS patch version: {PATCH_VERSION}")
    patch_gradle(app_dir)
    patch_app_name(app_dir)
    patch_manifest(app_dir)
    patch_http_util(app_dir)
    patch_notification_manager(app_dir)
    patch_main_activity(app_dir)
    write_server_adapter(app_dir)
    write_constants(app_dir)
    write_layouts(app_dir)
    write_drawables(app_dir)
    install_resources(app_dir, kit_root)

    print("WPS v13 applied: WPS User-Agent and manual full subscription refresh.")
    print("No subscription URL is embedded in the application.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
