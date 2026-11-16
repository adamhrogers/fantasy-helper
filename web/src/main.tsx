import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { createApi } from './api/client';
import { supabaseAuth } from './auth/supabase';
import { ConfigProblem } from './components/Notices';
import { readConfig } from './config';
import { startServiceWorker } from './pwa';
import './styles.css';

const root = createRoot(document.getElementById('root')!);
const result = readConfig(import.meta.env);

if (result.ok) {
  const { config } = result;
  const auth = supabaseAuth(config.supabaseUrl, config.supabasePublishableKey);
  const api = createApi({ baseUrl: config.apiUrl, getToken: () => auth.accessToken() });
  root.render(
    <StrictMode>
      <App auth={auth} api={api} codeSignIn={config.codeSignIn} />
    </StrictMode>,
  );
  startServiceWorker();
} else {
  root.render(<ConfigProblem problems={result.problems} />);
}
