import type { Metadata } from "next";
import { ReviewerExperience } from "../components/ReviewerExperience";

export const metadata: Metadata = {
  title: "심사위원 체험",
  description: "MESIL_Chat AI 챌린지 심사위원용 가명자료 체험 안내",
  robots: {
    index: false,
    follow: false,
    nocache: true,
  },
};

export default function ReviewerPage() {
  return <ReviewerExperience />;
}
