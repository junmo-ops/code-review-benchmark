import { createContext, useContext, useLayoutEffect, useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { App as AntApp, ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';

const AppearanceContext = createContext<{ dark: boolean; setDark: (dark: boolean) => void } | null>(null);

export function useAppearance() {
  const appearance = useContext(AppearanceContext);
  if (!appearance) throw new Error('主题上下文不可用');
  return appearance;
}

function ThemeSurface({ children, dark }: { children: ReactNode; dark: boolean }) {
  const { token } = theme.useToken();
  const style = useMemo(() => ({
    '--bench-container': token.colorBgContainer,
    '--bench-fill': token.colorFillQuaternary,
    '--bench-border': token.colorBorder,
    '--bench-split': token.colorSplit,
    '--bench-secondary': token.colorTextSecondary,
    '--bench-scrollbar': token.colorTextQuaternary,
    '--bench-primary': token.colorPrimary,
    color: token.colorText,
    background: token.colorBgLayout,
    colorScheme: dark ? 'dark' : 'light',
    minHeight: '100vh',
  } as CSSProperties), [token, dark]);
  useLayoutEffect(() => {
    // Dialogs render in document.body, outside this wrapper: share content tokens with them.
    const root = document.documentElement.style;
    const entries = Object.entries(style).filter(([name]) => name.startsWith('--bench-'));
    const previous = entries.map(([name]) => [name, root.getPropertyValue(name)]);
    for (const [name, value] of entries) root.setProperty(name, String(value));
    return () => {
      for (const [name, value] of previous) {
        if (value) root.setProperty(name, value);
        else root.removeProperty(name);
      }
    };
  }, [style]);
  return <div style={style}>{children}</div>;
}

export function WorkbenchTheme({ children, nonce }: { children: ReactNode; nonce?: string }) {
  // Each page load starts with Ant Design's default light theme.
  const [dark, setDark] = useState(false);
  return (
    <AppearanceContext.Provider value={{ dark, setDark }}>
      <ConfigProvider
        locale={zhCN}
        csp={nonce ? { nonce } : undefined}
        theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm }}
      >
        <AntApp>
          <ThemeSurface dark={dark}>{children}</ThemeSurface>
        </AntApp>
      </ConfigProvider>
    </AppearanceContext.Provider>
  );
}
