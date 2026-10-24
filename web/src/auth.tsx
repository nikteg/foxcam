// Who is signed in, and what they may do. The server enforces all of it; the UI only
// hides what would be refused anyway.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, setUnauthorizedHandler, type AuthState, type Camera, type CameraRole } from "./api";

export const AUTH_KEY = ["auth"] as const;

export function useAuth() {
  return useQuery({ queryKey: AUTH_KEY, queryFn: api.authState, staleTime: 60_000 });
}

/** The auth state once loaded (inside the signed-in app it always is). */
export function useMe(): AuthState | undefined {
  return useAuth().data;
}

export function useIsAdmin(): boolean {
  return useMe()?.is_admin ?? false;
}

/** After signing in or out: everything cached belonged to someone else. The auth entry
 * itself is updated in place, not removed: the screens watching it (sign-in, setup) must
 * see the new state, and `clear()` would detach them from the cache. */
export function useAuthChanged() {
  const client = useQueryClient();
  return (state?: AuthState) => {
    client.removeQueries({ predicate: (q) => q.queryKey[0] !== AUTH_KEY[0] });
    if (state) client.setQueryData(AUTH_KEY, state);
    else void client.invalidateQueries({ queryKey: AUTH_KEY });
  };
}

/** Re-check the login whenever the server answers 401 (session expired, signed out
 * elsewhere, account disabled). */
export function useUnauthorizedRedirect() {
  const client = useQueryClient();
  useEffect(() => {
    setUnauthorizedHandler(() => void client.invalidateQueries({ queryKey: AUTH_KEY }));
  }, [client]);
}

const RANK: Record<CameraRole, number> = { viewer: 0, editor: 1, owner: 2 };

export function hasRole(camera: Pick<Camera, "role"> | undefined, wanted: CameraRole): boolean {
  return camera !== undefined && RANK[camera.role] >= RANK[wanted];
}

/** The user's role on a camera, from the camera list. */
export function useCameraRole(cameraId: string | undefined): CameraRole | undefined {
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  return cameras.data?.find((c) => c.id === cameraId)?.role;
}
