import { createRoot } from 'react-dom/client';
import { WorkbenchTheme } from './theme';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import 'antd/dist/reset.css';
import './styles.css';
const nonce = document.querySelector<HTMLMetaElement>('meta[name="style-nonce"]')?.content;
createRoot(document.getElementById('root')!).render(
  <WorkbenchTheme nonce={nonce}>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </WorkbenchTheme>,
);
