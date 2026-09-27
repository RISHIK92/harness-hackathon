// [MOCK DATA] Condensed excerpt of Adventa's real Smart Mock Test logic —
// the personalized mock test that targets each student's actual weak spots
// instead of a generic paper.
import type { Request, Response } from "express";
import { prisma } from "../services/db.js";

type ExamDetails = {
  totalQuestions: number;
  marksPerCorrect: number;
  negativeMarksPerIncorrect: number;
};

export const createSmartMockTest = async (req: Request, res: Response) => {
  try {
    const { uid } = req.user;
    const { examId } = req.params;
    const numericExamId = parseInt(examId, 10);

    if (isNaN(numericExamId)) {
      return res
        .status(400)
        .json({ success: false, error: "A valid numeric Exam ID is required." });
    }

    // A Smart Mock is only as good as the topic-level accuracy data behind
    // it. Below this many completed mocks there just isn't enough signal
    // to personalize on, so the student gets a Diagnostic Test instead —
    // never a blocked screen with no path forward.
    const MIN_TESTS_FOR_SMART_MOCK = 3;

    const [userSummary, examDetails] = await Promise.all([
      prisma.userExamOverallSummary.findUnique({
        where: { userId_examId: { userId: uid, examId: numericExamId } },
      }),
      prisma.exam.findUnique({
        where: { id: numericExamId },
        select: {
          totalQuestions: true,
          marksPerCorrect: true,
          negativeMarksPerIncorrect: true,
        },
      }),
    ]);

    if (!examDetails) {
      return res
        .status(404)
        .json({ success: false, error: `Exam with ID ${numericExamId} not found.` });
    }

    if (!userSummary || userSummary.totalMockTestsCompleted < MIN_TESTS_FOR_SMART_MOCK) {
      const diagnosticTest = await generateDiagnosticTest(examDetails, numericExamId, uid);
      const testsNeeded =
        MIN_TESTS_FOR_SMART_MOCK - (userSummary?.totalMockTestsCompleted || 0);
      return res.status(202).json({
        success: true,
        mode: "DIAGNOSTIC",
        message: `Take ${testsNeeded} more general mock test(s) to unlock your personalized Smart Mock Test! We've generated a Diagnostic Test to get you started.`,
        data: { testInstanceId: diagnosticTest.id },
      });
    }

    // Enough signal exists — build a real Smart Mock from the student's
    // weakest topics and difficulty performance (see generateSmartMock).
    const smartMock = await generateSmartMock(examDetails, numericExamId, uid);
    return res.status(201).json({ success: true, mode: "SMART", data: smartMock });
  } catch (error) {
    console.error("createSmartMockTest error:", error);
    return res.status(500).json({ success: false, error: "Internal server error." });
  }
};

async function generateDiagnosticTest(
  examDetails: ExamDetails,
  examId: number,
  uid: string
) {
  // A fixed-shape general paper across every subject, evenly weighted —
  // there's no per-topic accuracy yet, so there's nothing to weight by.
  return prisma.testInstance.create({
    data: { userId: uid, examId, mode: "DIAGNOSTIC", totalQuestions: examDetails.totalQuestions },
  });
}

async function generateSmartMock(examDetails: ExamDetails, examId: number, uid: string) {
  return prisma.testInstance.create({
    data: { userId: uid, examId, mode: "SMART", totalQuestions: examDetails.totalQuestions },
  });
}
