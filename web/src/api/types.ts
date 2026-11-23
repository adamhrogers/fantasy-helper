// Names for the pieces of the API's schema the app uses. The schema itself (schema.d.ts) is
// generated from openapi.json by `npm run gen:api`; never edit it by hand.
import type { components } from './schema';

export type Me = components['schemas']['Me'];
export type League = components['schemas']['LeagueOut'];
export type ReportsOut = components['schemas']['ReportsOut'];
export type ReportDocument = components['schemas']['ReportDocument'];
export type Card = components['schemas']['Card'];
export type GameDay = components['schemas']['GameDay'];
export type ReportKind = 'daily' | 'weekly';
export type PushKey = components['schemas']['PushKey'];
/** What the API takes to save a device: only the address and the two keys, nothing else. */
export type SubscriptionBody = components['schemas']['SubscriptionIn'];
