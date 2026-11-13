import type { IdentityDeliveryInfo, IdentityLink, IdentitySummary } from '../types/models';
import { http } from './client';

export interface LinkQuery {
  status?: string;
  anonymous_key?: string;
  account_key?: string;
}

export const identityApi = {
  list: (query: LinkQuery = {}) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
    });
    const qs = params.toString();
    return http.get<{ items: IdentityLink[]; total: number; summary: IdentitySummary }>(
      qs ? `/identity/links?${qs}` : '/identity/links',
    );
  },
  summary: () => http.get<IdentitySummary>('/identity/summary'),
  deliver: (payload: { anonymous_key: string; account_key: string; note?: string }) =>
    http.post<{ link: IdentityLink; delivery: IdentityDeliveryInfo }>('/identity/links', payload),
  suspend: (id: number) => http.post<{ link: IdentityLink }>(`/identity/links/${id}/suspend`),
  resume: (id: number) => http.post<{ link: IdentityLink }>(`/identity/links/${id}/resume`),
  resolve: (id: number, action: 'activate' | 'suspend') =>
    http.post<{ link: IdentityLink }>(`/identity/links/${id}/resolve`, { action }),
};
