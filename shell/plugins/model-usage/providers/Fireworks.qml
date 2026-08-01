import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root
  visible: false

  property string providerId: "fireworks"
  property string providerName: "Fireworks"
  property string providerIcon: "ai"
  property bool enabled: false
  property bool ready: false
  property bool refreshing: false
  property double lastRefreshedAtMs: 0

  property real rateLimitPercent: -1
  property string rateLimitLabel: ""
  property string rateLimitResetAt: ""
  property real secondaryRateLimitPercent: -1
  property string secondaryRateLimitLabel: ""
  property string secondaryRateLimitResetAt: ""

  property int todayPrompts: 0
  property int todaySessions: 0
  property real todayTotalTokens: 0
  property var todayTokensByModel: ({})
  property bool hasPromptStats: false

  property var recentDays: []
  property int totalPrompts: 0
  property int totalSessions: 0
  property int activeDays: 0
  property var activeDates: []
  property var modelUsage: ({})

  property real balanceRemaining: -1
  property real balanceFunded: -1
  property real balanceSpent: -1
  property string balanceCurrency: "USD"
  property bool balanceEstimated: true

  property string tierLabel: "Prepaid"
  property string usageStatusText: ""
  property string authHelpText: "Set FIREWORKS_API_KEY or run `firectl set-api-key`."
  property bool hasLocalStats: true
  property var providerSettings: ({})

  readonly property string scannerPath: String(Qt.resolvedUrl("../scripts/fireworks_usage_scanner.py")).replace("file://", "")

  Process {
    id: usageScanner
    command: root.scannerCommand()
    running: false

    stdout: StdioCollector {
      onStreamFinished: root.parseScannerOutput(text)
    }

    stderr: StdioCollector {
      onStreamFinished: if (text.trim() !== "") console.warn("model-usage/fireworks", text.trim())
    }

    onExited: root.finishRefresh()
  }

  onEnabledChanged: if (enabled) refresh()
  onProviderSettingsChanged: if (enabled) refresh()

  function setting(name, fallback) {
    var value = providerSettings ? providerSettings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function scannerCommand() {
    return [
      "python3",
      root.scannerPath,
      "--account-id", String(setting("accountId", "")),
      "--funded-amount", String(setting("fundedAmount", 0)),
      "--funded-at", String(setting("fundedAt", "")),
      "--auth-path", String(setting("authPath", "~/.fireworks/auth.ini"))
    ]
  }

  function finishRefresh() {
    root.refreshing = false
    root.lastRefreshedAtMs = Date.now()
  }

  function refresh(force) {
    if (usageScanner.running) return
    root.refreshing = true
    usageScanner.command = root.scannerCommand()
    usageScanner.running = true
  }

  function refreshLimits() { refresh() }

  function parseScannerOutput(output) {
    var raw = String(output || "").trim()
    if (raw === "") return

    try {
      var data = JSON.parse(raw.split("\n").pop())
      root.usageStatusText = data.usageStatusText || ""
      root.authHelpText = data.authHelpText || ""
      if (!data.ready) return

      root.ready = true
      root.hasLocalStats = data.hasLocalStats !== false
      root.hasPromptStats = data.hasPromptStats === true
      root.todayPrompts = Number(data.todayPrompts || 0)
      root.todaySessions = Number(data.todaySessions || 0)
      root.todayTotalTokens = Number(data.todayTotalTokens || 0)
      root.todayTokensByModel = data.todayTokensByModel || ({})
      root.recentDays = data.recentDays || []
      root.totalPrompts = Number(data.totalPrompts || 0)
      root.totalSessions = Number(data.totalSessions || 0)
      root.activeDays = Number(data.activeDays || 0)
      root.activeDates = data.activeDates || []
      root.modelUsage = data.modelUsage || ({})
      root.balanceRemaining = Number(data.balanceRemaining ?? -1)
      root.balanceFunded = Number(data.balanceFunded ?? -1)
      root.balanceSpent = Number(data.balanceSpent ?? -1)
      root.balanceCurrency = data.balanceCurrency || "USD"
      root.balanceEstimated = data.balanceEstimated !== false
      root.tierLabel = data.tierLabel || "Prepaid"
    } catch (error) {
      console.error("model-usage/fireworks", "Failed to parse scanner output:", error, raw)
      root.usageStatusText = "Fireworks scan failed"
      root.authHelpText = String(error)
    }
  }

  function formatResetTime(isoTimestamp) { return "" }
}
