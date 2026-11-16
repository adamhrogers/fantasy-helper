// Builds the `_headers` file Cloudflare serves (see headers.template). The two addresses the app may
// call come from the same build variables the app itself reads, so the policy and the app agree
// and no address is written in this public repo. With a variable missing the app shows its "build
// isn't set up" screen anyway, and the policy then allows no outside address at all.
import { origin } from './src/config';

export function renderHeaders(template: string, env: Record<string, unknown>): string {
  const allowed = [origin(env.VITE_API_URL), origin(env.VITE_SUPABASE_URL)].filter(
    (address): address is string => address !== null,
  );
  return template.replace('{{CONNECT_ORIGINS}}', allowed.join(' '));
}
