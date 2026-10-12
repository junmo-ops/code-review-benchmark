import { createRoot } from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles.css';
const nonce = document.querySelector<HTMLMetaElement>('meta[name="style-nonce"]')?.content;
createRoot(document.getElementById('root')!).render(
  <ConfigProvider
    locale={zhCN}
    csp={nonce ? { nonce } : undefined}
    theme={{
      token: {
        colorPrimary: '#147d70',
        colorInfo: '#2876c7',
        colorSuccess: '#16836f',
        colorWarning: '#d99022',
        colorError: '#c94d56',
        colorText: '#26384d',
        colorTextSecondary: '#748396',
        colorBorder: '#dfe6ed',
        colorBgLayout: '#f4f7fb',
        colorBgContainer: '#ffffff',
        borderRadius: 8,
        controlHeight: 38,
        fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      },
      components: {
        Layout: {
          bodyBg: '#f4f7fb',
          headerBg: '#ffffff',
          siderBg: '#152535',
        },
        Menu: {
          darkItemBg: '#152535',
          darkItemColor: '#bac7d2',
          darkItemHoverBg: '#203849',
          darkItemSelectedBg: '#174b4b',
          darkItemSelectedColor: '#c2f1e9',
          itemBorderRadius: 8,
          itemHeight: 42,
          itemMarginBlock: 3,
          itemMarginInline: 0,
        },
        Card: {
          borderRadiusLG: 10,
          headerHeight: 52,
        },
        Table: {
          headerBg: '#f6f8fa',
          headerColor: '#66788a',
          rowHoverBg: '#f5faf9',
          borderColor: '#e8edf2',
        },
        Button: {
          borderRadius: 8,
          controlHeight: 38,
        },
        Input: {
          borderRadius: 8,
          controlHeight: 38,
        },
        Select: {
          borderRadius: 8,
          controlHeight: 38,
        },
      },
    }}
  >
    <AntApp>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AntApp>
  </ConfigProvider>,
);
