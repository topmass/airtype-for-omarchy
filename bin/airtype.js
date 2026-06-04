#!/usr/bin/env node
import { spawnSync } from "node:child_process"
import { mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const rootDir = dirname(dirname(fileURLToPath(import.meta.url)))
const isWindows = process.platform === "win32"
const args = process.argv.slice(2)

function commandExists(command) {
  const result = spawnSync(command, ["--version"], {
    stdio: "ignore",
    shell: isWindows,
  })
  return result.status === 0
}

function run(command, commandArgs) {
  const result = spawnSync(command, commandArgs, {
    cwd: rootDir,
    env: airtypeEnv(),
    stdio: "inherit",
    shell: isWindows,
  })
  process.exit(result.status ?? 1)
}

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

if (!commandExists("uv")) {
  console.error("Airtype needs uv to manage its Python speech engine.")
  console.error("Install uv first: https://docs.astral.sh/uv/getting-started/installation/")
  process.exit(1)
}

if (args.length === 0 || args[0] === "tui") {
  if (!commandExists("bun")) {
    console.error("Airtype's TUI currently needs Bun because OpenTUI is Bun-first.")
    console.error("Install Bun first: https://bun.com/docs/installation")
    console.error("Then run: airtype")
    process.exit(1)
  }
  run("bun", [join(rootDir, "src", "tui", "airtype-tui.ts")])
}

run("uv", ["run", "--project", rootDir, "airtype", ...args])
