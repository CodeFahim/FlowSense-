"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Loading } from "@/components/ui";

/** Password resets now use a 6-digit code on the "Forgot password" page; old links land there. */
export default function ResetPasswordPage() {
  const router = useRouter();
  useEffect(() => {
    if (window.location.hash) history.replaceState(null, "", window.location.pathname);
    router.replace("/forgot-password");
  }, [router]);
  return <Loading what="Opening password reset" />;
}
