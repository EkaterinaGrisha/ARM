import { ClipboardCheck, Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useOutletContext } from "react-router-dom";
import { api } from "../api/client";
import type { Health } from "../api/types";

type Theme = "system" | "light" | "dark";
const THEME_KEY = "arm.theme";

function readTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    if (theme === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* хранилище недоступно — тема действует до перезагрузки */
  }
}

export interface ServerState {
  health: Health | null;
  offline: boolean;
  checked: boolean;
}

function useServerHealth(): ServerState {
  const [state, setState] = useState<ServerState>({ health: null, offline: false, checked: false });
  useEffect(() => {
    let alive = true;
    const check = () =>
      api
        .health()
        .then((h) => alive && setState({ health: h, offline: false, checked: true }))
        .catch(() => alive && setState({ health: null, offline: true, checked: true }));
    check();
    const t = setInterval(check, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  return state;
}

export function Layout() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const server = useServerHealth();
  const { health, offline, checked } = server;
  useEffect(() => applyTheme(theme), [theme]);
  const nextTheme: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
  const themeIcon = theme === "light" ? <Sun size={17} /> : theme === "dark" ? <Moon size={17} /> : <Monitor size={17} />;
  const themeLabel = { system: "Тема: как в системе", light: "Тема: светлая", dark: "Тема: тёмная" }[theme];

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand">
            <span className="brand-mark">
              <ClipboardCheck size={17} />
            </span>
            <span className="brand-text">
              Входной контроль КД
              <small>АРМ · комплектность и анализ чертежей</small>
            </span>
          </NavLink>
          <nav className="nav" aria-label="Разделы">
            <NavLink to="/" end>
              Проверки
            </NavLink>
            <NavLink to="/new">
              Новая <span className="label-long">проверка</span>
            </NavLink>
            <NavLink to="/jobs/demo">
              Демо<span className="label-long">-отчёт</span>
            </NavLink>
          </nav>
          <div className="topbar-right">
            {checked && (
              <span
                className="server-pill"
                title={
                  health
                    ? `Конвейер: ${health.pipeline} ${health.pipeline_version}; OCR сканов: ${health.ocr ? "включён" : "недоступен"}`
                    : "API не отвечает: доступны демо-отчёт и просмотр report.json из файла"
                }
              >
                <i className={`dot ${offline ? "bad" : "good"}`} />
                <span className="label">{offline ? "Сервер недоступен" : `Сервер · ${health?.ocr ? "OCR вкл." : "без OCR"}`}</span>
              </span>
            )}
            <button className="btn icon ghost" onClick={() => setTheme(nextTheme[theme])} title={themeLabel} aria-label={themeLabel}>
              {themeIcon}
            </button>
          </div>
        </div>
      </header>
      <main className="container">
        <Outlet context={server} />
      </main>
    </>
  );
}

/** Состояние сервера, которое проверяет шапка приложения. */
export function useServer(): ServerState {
  return useOutletContext<ServerState>();
}
