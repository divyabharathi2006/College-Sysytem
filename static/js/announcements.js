(() => {
    const audience = document.getElementById('target-type');
    const departmentTarget = document.getElementById('department-target');
    const department = document.getElementById('department-id');
    const sectionTarget = document.getElementById('section-target');
    const section = document.getElementById('section-id');
    if (!audience) return;

    const updateTargets = () => {
        const scope = audience.value;
        const departmentEnabled = scope === 'department';
        const sectionEnabled = scope === 'section';
        if (departmentTarget) {
            departmentTarget.hidden = !departmentEnabled;
            if (department) {
                department.disabled = !departmentEnabled;
                department.required = departmentEnabled && department.tagName === 'SELECT';
            }
        }
        if (sectionTarget) {
            sectionTarget.hidden = !sectionEnabled;
            if (section) {
                section.disabled = !sectionEnabled;
                section.required = sectionEnabled;
            }
        }
    };

    audience.addEventListener('change', updateTargets);
    updateTargets();
})();
