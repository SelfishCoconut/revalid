import { useParams } from "react-router-dom";

import { RetestSession } from "./RetestSession";

/** URL-param wrapper so /retest-sessions/:id keeps working as a deep link. */
export function RetestSessionRoute() {
  return <RetestSession sessionId={Number(useParams().id)} />;
}
