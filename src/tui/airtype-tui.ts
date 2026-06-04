import { BoxRenderable, TextRenderable, TextAttributes, createCliRenderer } from "@opentui/core"
import { ChildProcessWithoutNullStreams, spawn, spawnSync } from "node:child_process"
import { mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { createInterface } from "node:readline"

type BackendEvent = {
  event?: string
  state?: string
  hotkey?: string
  listener?: string
  message?: string
  result?: {
    text?: string
    copied?: boolean
    pasted?: boolean
    paste_backend?: string
    elapsed_seconds?: number
    loaded_now?: boolean
    unloaded?: boolean
  }
}

const rootDir = new URL("../..", import.meta.url).pathname

function airtypeCacheDir() {
  if (process.platform === "win32") {
    return join(process.env.LOCALAPPDATA || join(homedir(), "AppData", "Local"), "airtype")
  }
  if (process.platform === "darwin") {
    return join(homedir(), "Library", "Caches", "airtype")
  }
  return join(process.env.XDG_CACHE_HOME || join(homedir(), ".cache"), "airtype")
}

function airtypeEnv() {
  const env = { ...process.env }
  if (!env.UV_PROJECT_ENVIRONMENT) {
    const cacheDir = airtypeCacheDir()
    mkdirSync(cacheDir, { recursive: true })
    env.UV_PROJECT_ENVIRONMENT = join(cacheDir, "uv-venv")
  }
  return env
}

type Settings = {
  paste_mode: string
  paste_label: string
  hotkey: string
  model_dir: string
  model_exists: boolean
}

function runAirtype(args: string[], stdio: "pipe" | "inherit" = "pipe") {
  return spawnSync("uv", ["run", "--project", rootDir, "airtype", ...args], {
    cwd: rootDir,
    encoding: "utf8",
    env: airtypeEnv(),
    stdio,
  })
}

function requireModelSetup(current: Settings) {
  if (process.env.AIRTYPE_TUI_SKIP_SETUP === "1") return
  if (current.model_exists) return
  const setup = runAirtype(["setup"], "inherit")
  if (setup.status !== 0) {
    process.exit(setup.status ?? 1)
  }
}

function loadSettings(): Settings {
  const result = runAirtype(["settings", "--json"])
  if (result.status !== 0 || !result.stdout) {
    throw new Error(result.stderr || "Could not load Airtype settings")
  }
  return JSON.parse(result.stdout) as Settings
}

function cycleSetting(kind: "paste" | "hotkey"): Settings {
  const flag = kind === "paste" ? "--cycle-paste" : "--cycle-hotkey"
  const result = runAirtype(["settings", flag, "--json"])
  if (result.status !== 0 || !result.stdout) {
    throw new Error(result.stderr || `Could not update ${kind}`)
  }
  return JSON.parse(result.stdout) as Settings
}

let settings = loadSettings()
requireModelSetup(settings)
settings = loadSettings()
let uv: ChildProcessWithoutNullStreams

function serviceArgs() {
  const args = [
    "run",
    "--project",
    rootDir,
    "airtype",
    "service",
    "--unload-timeout",
    process.env.AIRTYPE_TUI_UNLOAD_TIMEOUT ?? "0",
  ]
  if (process.env.AIRTYPE_TUI_PASTE) {
    args.push("--paste", process.env.AIRTYPE_TUI_PASTE)
  }
  if (process.env.AIRTYPE_TUI_NO_COPY === "1") {
    args.push("--no-copy")
  }
  return args
}

function startBackend() {
  const child = spawn("uv", serviceArgs(), {
    cwd: rootDir,
    env: airtypeEnv(),
    stdio: ["pipe", "pipe", "pipe"],
  })

  createInterface({ input: child.stdout }).on("line", (line) => {
    try {
      handleEvent(JSON.parse(line))
    } catch {
      detail = line
      render()
    }
  })

  createInterface({ input: child.stderr }).on("line", (line) => {
    detail = line
    render()
  })

  child.on("exit", (code) => {
    if (uv === child) {
      status = "backend exited"
      substatus = `Exit code ${code ?? 0}`
      busy = false
      render()
    }
  })

  return child
}

let status = "Starting"
let substatus = "Launching backend"
let transcript = ""
let detail = ""
let hotkey = settings.hotkey
let listener = "loading"
let busy = false

const renderer = await createCliRenderer({
  exitOnCtrlC: false,
  targetFps: 12,
  consoleMode: "disabled",
  openConsoleOnError: false,
})

const panel = new BoxRenderable(renderer, {
  id: "airtype-panel",
  width: "100%",
  height: "100%",
  borderStyle: "rounded",
  borderColor: "#6EE7B7",
  padding: 1,
  flexDirection: "column",
  gap: 1,
})
const title = new TextRenderable(renderer, {
  id: "airtype-title",
  content: "Airtype",
  fg: "#6EE7B7",
  attributes: TextAttributes.BOLD,
})
const statusText = new TextRenderable(renderer, {
  id: "airtype-status",
  content: "",
  fg: "#F8FAFC",
})
const hintText = new TextRenderable(renderer, {
  id: "airtype-hint",
  content: "",
  fg: "#94A3B8",
})
const transcriptText = new TextRenderable(renderer, {
  id: "airtype-transcript",
  content: "",
  fg: "#E2E8F0",
})
const detailText = new TextRenderable(renderer, {
  id: "airtype-detail",
  content: "",
  fg: "#A7F3D0",
})

panel.add(title)
panel.add(statusText)
panel.add(hintText)
panel.add(transcriptText)
panel.add(detailText)
renderer.root.add(panel)

function render() {
  const indicator = busy ? "●" : "○"
  statusText.content = `${indicator} ${status}`
  hintText.content = `${substatus} | Double-tap ${hotkey} globally | Enter toggles | p paste | h hotkey | q quits`
  transcriptText.content = transcript ? `Last: ${transcript}` : "Last: "
  detailText.content = detail || `Listener: ${listener} | Paste: ${settings.paste_label} | Model: ${settings.model_dir}`
  renderer.requestRender()
}

function handleEvent(data: BackendEvent) {
  if (data.hotkey) hotkey = data.hotkey
  if (data.listener) listener = data.listener
  if (data.state) {
    status = data.state
    busy = data.state === "recording" || data.state === "transcribing"
  }
  if (data.message) substatus = data.message
  if (data.event === "ready") {
    status = "ready"
    substatus = `Global listener: ${listener}`
    busy = false
  }
  if (data.event === "transcript" && data.result) {
    transcript = data.result.text || ""
    const copied = data.result.copied ? "copied" : "not copied"
    const pasted = data.result.pasted ? `pasted:${data.result.paste_backend}` : "not pasted"
    const unloaded = data.result.unloaded ? "unloaded" : "kept loaded"
    detail = `${data.result.elapsed_seconds?.toFixed(2) ?? "0.00"}s | ${copied} | ${pasted} | ${unloaded}`
    substatus = "Ready"
    busy = false
  }
  render()
}

function send(command: string) {
  if (!uv.killed) {
    uv.stdin.write(`${JSON.stringify({ command })}\n`)
  }
}

function restartBackend() {
  const old = uv
  send("quit")
  old.kill()
  status = "Starting"
  substatus = "Restarting backend"
  listener = "loading"
  uv = startBackend()
  render()
}

function updateSetting(kind: "paste" | "hotkey") {
  if (busy) {
    detail = "Finish the current recording before changing settings"
    render()
    return
  }
  try {
    settings = cycleSetting(kind)
    hotkey = settings.hotkey
    detail = `Paste: ${settings.paste_label} | Hotkey: ${settings.hotkey}`
    restartBackend()
  } catch (error) {
    detail = error instanceof Error ? error.message : String(error)
    render()
  }
}

async function shutdown(code = 0) {
  send("quit")
  uv.kill()
  renderer.destroy()
  await new Promise<void>((resolve) => queueMicrotask(resolve))
  process.exit(code)
}

renderer.addInputHandler((sequence) => {
  if (sequence === "q" || sequence === "Q") {
    shutdown(0)
    return true
  }
  if (sequence === "\r" || sequence === "\n" || sequence === " ") {
    send("toggle")
    return true
  }
  if (sequence === "p" || sequence === "P") {
    updateSetting("paste")
    return true
  }
  if (sequence === "h" || sequence === "H") {
    updateSetting("hotkey")
    return true
  }
  if (sequence === "\u0003") {
    shutdown(0)
    return true
  }
  return false
})

process.on("SIGINT", () => shutdown(0))
process.on("SIGTERM", () => shutdown(0))

uv = startBackend()
render()
