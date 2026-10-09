document.addEventListener('DOMContentLoaded', function () {
    const current = document.getElementById('currentAttendance');
    const upcoming = document.getElementById('upcomingClasses');
    const expected = document.getElementById('expectedAttendance');
    const resultBox = document.getElementById('predictionResult');
    const button = document.getElementById('simulateButton');
    const total = Number(document.getElementById('predictionData').dataset.total) || 0;
    const target = Number(document.getElementById('predictionData').dataset.target) || 75;
    const cells = {
        current: document.getElementById('simulationCurrent'),
        projected: document.getElementById('simulationProjected'),
        target: document.getElementById('simulationTarget'),
        risk: document.getElementById('simulationRisk'),
        required: document.getElementById('simulationRequired'),
    };

    function riskFor(percentage) {
        if (percentage >= 90) return 'SAFE';
        if (percentage >= 75) return 'NORMAL';
        if (percentage >= 65) return 'WARNING';
        if (percentage >= 50) return 'HIGH RISK';
        return 'CRITICAL';
    }

    function compute() {
        const currentPercentage = Math.max(0, Math.min(100, Number(current.value) || 0));
        const courses = Math.max(0, Number(upcoming.value) || 0);
        const expectedValue = Math.max(0, Math.min(courses, Number(expected.value) || 0));
        const adjustedPresent = total > 0 ? currentPercentage * total / 100 : 0;
        const projected = total + courses === 0 ? 0 : ((adjustedPresent + expectedValue) / (total + courses)) * 100;
        const required = currentPercentage >= target || total === 0
            ? 0
            : Math.max(0, Math.ceil((target * total / 100 - adjustedPresent) / (1 - target / 100)));
        const risk = riskFor(projected);
        cells.current.textContent = `${currentPercentage.toFixed(2)}%`;
        cells.projected.textContent = `${projected.toFixed(2)}%`;
        cells.target.textContent = `${target.toFixed(2)}%`;
        cells.risk.textContent = risk;
        cells.required.textContent = String(required);
        resultBox.textContent = `Current: ${currentPercentage.toFixed(2)}%; projected: ${projected.toFixed(2)}%; target: ${target.toFixed(2)}%; risk: ${risk}; attend ${required} consecutive classes to reach the target.`;
    }

    button.addEventListener('click', compute);
    current.addEventListener('input', compute);
    upcoming.addEventListener('input', compute);
    expected.addEventListener('input', compute);
    upcoming.addEventListener('input', function () {
        expected.max = upcoming.value;
        if (Number(expected.value) > Number(upcoming.value)) expected.value = upcoming.value;
        compute();
    });
    compute();
});
