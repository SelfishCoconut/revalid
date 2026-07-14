import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approvePlan,
  editPlan,
  generatePlan,
  listPlans,
  rejectPlan,
  retest,
} from "../api/client";
import type { Plan, PlannedAction, Verdict } from "../api/types";
import { queryKeys } from "./queryKeys";

/** Every plan version for a finding (the audit trail). */
export function usePlans(findingId: number, enabled = true) {
  return useQuery({
    queryKey: queryKeys.plans(findingId),
    queryFn: () => listPlans(findingId),
    enabled: enabled && Number.isFinite(findingId),
  });
}

function usePlanMutation<TArgs>(
  findingId: number,
  mutationFn: (args: TArgs) => Promise<Plan>,
) {
  const client = useQueryClient();
  return useMutation<Plan, Error, TArgs>({
    mutationFn,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.plans(findingId) });
    },
  });
}

export function useGeneratePlan(findingId: number) {
  return usePlanMutation<void>(findingId, () => generatePlan(findingId));
}

export function useEditPlan(findingId: number) {
  return usePlanMutation<PlannedAction[]>(findingId, (actions) =>
    editPlan(findingId, actions),
  );
}

export function useApprovePlan(findingId: number) {
  return usePlanMutation<void>(findingId, () => approvePlan(findingId));
}

export function useRejectPlan(findingId: number) {
  return usePlanMutation<void>(findingId, () => rejectPlan(findingId));
}

/** Run the approved plan; refreshes both plans and verdicts on success. */
export function useRetest(findingId: number) {
  const client = useQueryClient();
  return useMutation<Verdict[], Error, void>({
    mutationFn: () => retest(findingId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.verdicts });
      void client.invalidateQueries({ queryKey: queryKeys.plans(findingId) });
    },
  });
}
