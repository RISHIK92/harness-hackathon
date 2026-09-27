// [MOCK DATA] Condensed excerpt of Adventa's real Weakness Test logic — a
// short test built from the topics a student is actually struggling with,
// with a before/after accuracy comparison once they retake it.
import type { Request, Response } from "express";
import { prisma } from "../services/db.js";

export const generateWeaknessTest = async (req: Request, res: Response) => {
  const { uid } = req.user;
  const { examId } = req.body;

  if (!uid) return res.status(401).json({ error: "User not authenticated" });
  if (!examId) return res.status(400).json({ error: "Exam ID is required" });

  const exam = await prisma.exam.findUnique({ where: { id: examId } });
  if (!exam) return res.status(404).json({ error: "Exam not found" });

  // === STEP 1: Find the 2 weakest topics per subject, by accuracy ===
  // totalAttempted >= 3 on purpose: a topic with one unlucky question
  // isn't a weakness, it's noise. Without this floor the test would keep
  // re-serving whatever the student happened to guess wrong on once.
  const allUserTopicPerformance = await prisma.userTopicPerformance.findMany({
    where: {
      userId: uid,
      totalAttempted: { gte: 3 },
      topic: { subject: { examId: exam.id } },
    },
    include: { topic: { include: { subject: true } } },
    orderBy: { accuracyPercent: "asc" },
  });

  const topicsBySubject = allUserTopicPerformance.reduce((acc, perf) => {
    const subjectId = perf.topic.subject.id;
    if (!acc[subjectId]) acc[subjectId] = [];
    acc[subjectId].push(perf);
    return acc;
  }, {} as Record<number, typeof allUserTopicPerformance>);

  const selectedTopicsForTest: {
    topicId: number;
    accuracyPercentBefore: number;
    totalAttemptedBefore: number;
  }[] = [];

  Object.values(topicsBySubject).forEach((performancesInSubject) => {
    const topWeakestTopics = performancesInSubject.slice(0, 2); // Take top 2
    topWeakestTopics.forEach((perf) => {
      selectedTopicsForTest.push({
        topicId: perf.topicId,
        accuracyPercentBefore: Number(perf.accuracyPercent),
        totalAttemptedBefore: perf.totalAttempted,
      });
    });
  });

  if (selectedTopicsForTest.length === 0) {
    return res
      .status(400)
      .json({ error: "Not enough performance data to generate a weakness test." });
  }

  const testInstance = await prisma.testInstance.create({
    data: { userId: uid, examId, mode: "WEAKNESS" },
  });

  return res.status(201).json({ success: true, data: { testInstanceId: testInstance.id, selectedTopicsForTest } });
};

export const getAccuracyComparison = async (req: Request, res: Response) => {
  // Compares accuracyPercentBefore (captured at test creation, above) to
  // the student's current accuracy on the same topics — "did the
  // weakness test actually help" is the whole point of showing this.
  const { uid } = req.user;
  const { topicIds } = req.body as { topicIds: number[] };

  const before = await prisma.userTopicPerformanceSnapshot.findMany({
    where: { userId: uid, topicId: { in: topicIds } },
  });
  const after = await prisma.userTopicPerformance.findMany({
    where: { userId: uid, topicId: { in: topicIds } },
  });

  const comparison = before.map((snapshot) => {
    const afterPerf = after.find((p) => p.topicId === snapshot.topicId);
    const accuracyAfter = afterPerf ? afterPerf.accuracyPercent : snapshot.accuracyPercentBefore;
    return {
      topicId: snapshot.topicId,
      accuracyBefore: Number(snapshot.accuracyPercentBefore).toFixed(2),
      accuracyAfter: Number(accuracyAfter).toFixed(2),
      delta: (Number(accuracyAfter) - Number(snapshot.accuracyPercentBefore)).toFixed(2),
    };
  });

  return res.status(200).json({ success: true, data: comparison });
};
