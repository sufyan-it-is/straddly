import { useEffect, useState } from 'react';

const LOGO_CACHE_KEY = 'tn_uploaded_logo';

let cachedLogo: string | null = null;
let logoRequest: Promise<string> | null = null;

async function fetchPortalLogo(): Promise<string> {
  if (cachedLogo) return cachedLogo;
  if (!logoRequest) {
    let stored = '';
    try {
      stored = localStorage.getItem(LOGO_CACHE_KEY) || '';
    } catch {
      // ignore storage access errors
    }

    logoRequest = fetch('/api/v2/admin/logo')
      .then(async (res) => {
        if (!res.ok) return stored;
        const data = await res.json().catch(() => ({}));
        const logo = typeof data?.logo === 'string' ? data.logo.trim() : '';
        return logo || stored;
      })
      .catch(() => stored)
      .then((logo) => {
        cachedLogo = logo || null;
        if (logo) {
          try {
            localStorage.setItem(LOGO_CACHE_KEY, logo);
          } catch {
            // ignore storage write errors
          }
        }
        return logo || '';
      });
  }
  return logoRequest;
}

export function usePortalLogo(): string {
  const [logo, setLogo] = useState<string>(cachedLogo || '');

  useEffect(() => {
    let isMounted = true;
    fetchPortalLogo().then((value) => {
      if (isMounted) setLogo(value);
    });
    return () => {
      isMounted = false;
    };
  }, []);

  return logo;
}
