import { useNavigate } from "react-router-dom";
import { errorText, useCommand } from "../api/client";
import { useToast } from "../components/Toast";

/** `＋ New sequence`: the server names it `Sequence N` and makes its output bin; we go there. */
export function useCreateSequence(slug: string | undefined) {
  const command = useCommand(slug);
  const navigate = useNavigate();
  const toast = useToast();

  const create = () => {
    if (!slug) return;
    command.mutate(
      { name: "create_sequence", body: {} },
      {
        onSuccess: (r) => {
          if (r.sequence) navigate(`/p/${encodeURIComponent(slug)}/s/${encodeURIComponent(r.sequence)}`);
        },
        onError: (e) => toast(errorText(e)),
      },
    );
  };

  return { create, pending: command.isPending };
}
