import { useCallback, useEffect, useRef, useState } from 'react';
import type { Api, ApiError, ApiResult } from './api/client';
import type { Me, ReportsOut } from './api/types';

/** What the screen has for one thing it asked the API for. After a failed refresh the data from
 *  before stays (with the error beside it), so a bad connection never hides a report you could read. */
export interface Resource<T> {
  data: T | null;
  error: ApiError | null;
  /** When `data` was loaded: what a failed refresh says it is still showing. */
  at: Date | null;
}

export interface Dashboard {
  me: Resource<Me>;
  daily: Resource<ReportsOut>;
  weekly: Resource<ReportsOut>;
  refreshing: boolean;
  refresh: () => void;
}

const EMPTY = { data: null, error: null, at: null };
const STALE_AFTER_MS = 5 * 60_000;

function merge<T>(before: Resource<T>, result: ApiResult<T>): Resource<T> {
  return result.ok
    ? { data: result.data, error: null, at: new Date() }
    : { data: before.data, error: result.error, at: before.at };
}

function fetchAll(api: Api) {
  return Promise.all([api.me(), api.reports('daily'), api.reports('weekly')]);
}

/** Loads who the user is and both reports when the app opens, again on request, and again when the
 *  app returns to the foreground after sitting for a few minutes (a phone app is rarely closed). */
export function useDashboard(api: Api): Dashboard {
  const [me, setMe] = useState<Resource<Me>>(EMPTY);
  const [daily, setDaily] = useState<Resource<ReportsOut>>(EMPTY);
  const [weekly, setWeekly] = useState<Resource<ReportsOut>>(EMPTY);
  const [refreshing, setRefreshing] = useState(true); // the first load starts as the app opens
  const generation = useRef(0);
  const lastLoad = useRef(0);

  const keep = useCallback(([meResult, dailyResult, weeklyResult]: Awaited<ReturnType<typeof fetchAll>>) => {
    setMe((before) => merge(before, meResult));
    setDaily((before) => merge(before, dailyResult));
    setWeekly((before) => merge(before, weeklyResult));
    lastLoad.current = Date.now();
    setRefreshing(false);
  }, []);

  const start = useCallback(() => {
    const mine = ++generation.current;
    void fetchAll(api).then((answers) => {
      if (mine === generation.current) keep(answers); // a newer load started: this answer is old
    });
  }, [api, keep]);

  const reload = useCallback(() => {
    setRefreshing(true);
    start();
  }, [start]);

  useEffect(() => {
    start();
  }, [start]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastLoad.current > STALE_AFTER_MS) {
        reload();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [reload]);

  return { me, daily, weekly, refreshing, refresh: reload };
}
